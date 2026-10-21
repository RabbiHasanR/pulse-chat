# Audio Workflow Architecture & Implementation Guide

## 1. High-Level Overview

This document details the end-to-end lifecycle of an audio message (voice note, music, podcast) in the application. We use a **Direct-to-S3** upload pattern combined with a **Zero-Download Streaming Processor** to ensure security, minimal latency, and low server costs.

### Key Features

* **Instant Playback:** Uses `MOOV Atom` relocation (`+faststart`) to allow streaming immediately, similar to Spotify/YouTube.
* **Visual Waveforms:** Generates a JSON array of amplitude peaks for a "WhatsApp-style" UI visualization.
* **Zero-Disk Processing:** Streams data directly from S3 to FFmpeg, allowing the processing of massive files (e.g., 2-hour podcasts) with minimal RAM/Disk usage.
* **Security:** Implements "Trust but Verify" with Magic Byte detection to block malicious uploads.

---

## 2. The Workflow (Step-by-Step)

### Phase 1: The Upload (Client  S3)

The backend does not receive the file bytes. We use a "Fan-Out" strategy.

1. **Request:** Client sends file metadata (name, size, type) to `POST /chats/upload/prepare/`.
2. **Sign:** Server generates a **Presigned S3 PUT URL**.
3. **Transfer:** Client uploads the file directly to AWS S3.
* *State:* The file is now in S3 but is considered **"Quarantined"**.
* *DB Status:* `processing_status = 'queued'`


4. **Complete:** Client calls `POST /chats/upload/complete/`. Server triggers the Background Worker.

### Phase 2: The Processing (Worker  S3)

The worker picks up the job asynchronously.

1. **Stream & Validate:** The worker opens a read-stream to S3 (fetching only the first 2KB initially).
* **Security Check:** Verifies "Magic Bytes" (File Signature). If it detects an executable/script, the file is **deleted immediately** and the job fails.


2. **Transcode (On-the-Fly):** FFmpeg pipes the input from S3 and converts it to **AAC (M4A)**.
* *Settings:* 64kbps, Mono, 44.1kHz.


3. **Waveform Generation:** Extracts amplitude peaks into a JSON array (e.g., `[0, 15, 40, 90, 30...]`).
4. **Cleanup:** The original "Quarantined" file is deleted from S3. The new, safe `.m4a` file is saved.
5. **Update:** DB is updated with `status='done'` and the waveform JSON.

### Phase 3: The Notification (Server  UI)

1. Worker sends a WebSocket event: `chat_message_update`.
2. Payload includes:
* `asset_id`
* `url` (The clean .m4a file)
* `waveform` (The JSON data)
* `duration`



---

## 3. Server-Side Implementation Details

### A. The "Zero-Download" Processor

Located in `server/utils/media_processors/audio_processor.py`.

**Why we don't download files:**
Downloading a 100MB podcast to disk takes time and space. Instead, we generate a Presigned **GET** URL and pass it to FFmpeg. FFmpeg reads the file over HTTP as it encodes, effectively acting as a pipeline.

**The FFmpeg Command:**

```python
(
    ffmpeg
    .input(presigned_url)       # Read from Network
    .output(
        output_path, 
        acodec='aac',           # Universally compatible codec
        audio_bitrate='64k',    # Optimized for voice
        ac=1,                   # Mono channel (50% size reduction)
        movflags='+faststart'   # <--- THE SECRET SAUCE
    )
    .run()
)

```

* **`+faststart`**: Moves the file metadata to the beginning. This tells the browser "Here is the duration and format" immediately, so it can start playing byte 0 while downloading byte 1000.

### B. Security Validation

We use `python-magic` (libmagic) to inspect the file headers.

* **Logic:** We fetch the first 2048 bytes via an S3 Range Request.
* **Rule:** If MIME type is `application/x-dosexec` (Windows EXE) or `text/x-php`, we raise a `ValueError` and trigger `_delete_from_s3`.

---

## 4. Frontend Implementation Guide (React/Mobile)

### A. Rendering the Player

Do **not** use HLS (.m3u8) for audio. Use the standard HTML5 Audio tag.

```jsx
// The 'variants' object comes from the WebSocket/API
const { url, waveform, duration } = asset.variants;

return (
  <div className="audio-bubble">
    <audio 
      src={url} 
      preload="metadata" 
      controls={false} // Hide default controls, build custom UI
    />
    <WaveformVisualizer data={waveform} />
  </div>
);

```

### B. The Waveform Visualizer

The backend sends an array like `[10, 50, 80, 40]`.

* **UI Logic:** Map these numbers to CSS `height` percentages.
* **Playing State:** When the audio is at 50%, color the first half of the bars blue and the rest gray.

```jsx
const WaveformVisualizer = ({ data }) => (
  <div className="flex items-center gap-[1px] h-8">
    {data.map((amplitude, i) => (
      <div 
        key={i} 
        style={{ height: `${amplitude}%` }} 
        className="w-1 bg-gray-400 rounded-full" 
      />
    ))}
  </div>
);

```

### C. Handling "Processing" State

1. **Upload Started:** Show "Uploading..." (Gray).
2. **WebSocket "Processing":** Show Spinner (Blue).
3. **WebSocket "Done":** Render the Player (Green/Theme Color).
4. **WebSocket "Failed":** Show Red Error Icon ("File corrupted").

---

## 5. Failure Recovery Strategy

* **Corrupt Files:** If FFmpeg fails to probe the file, the worker marks it as `failed` and notifies the UI. The user sees "Upload Failed".
* **Server Crash:** The Celery task is configured with `acks_late=True`. If the worker dies mid-process, the task is re-queued and retried by the next available worker.
* **Timeout:** If a file takes >15 minutes, `SoftTimeLimitExceeded` is raised, triggering a cleanup of temp files to prevent server clogging.