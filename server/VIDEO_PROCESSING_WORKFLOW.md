# Video Processing & HLS Workflow

## Overview
This document outlines the architecture for processing video uploads in Pulse Chat. The system transforms raw, large video files into optimized **HLS (HTTP Live Streaming)** playlists with adaptive bitrates (1080p, 720p, etc.). It features **Real-Time Progress Tracking**, allowing the frontend to display a smooth percentage loader to the user.

## Architecture Diagram


The workflow consists of three main components working in sync:
1.  **The Celery Worker (`tasks.py`):** The orchestrator that manages the job and throttles updates.
2.  **The Video Processor (`video_processor.py`):** The engine that runs FFmpeg to transcode video.
3.  **The Progress Tracker (`ffmpeg_progress.py`):** A TCP socket listener that calculates real-time percentage.

---

## 1. The Video Processor (`video_processor.py`)

### Strategy: Zero-Download Streaming
Instead of downloading a 1GB file to the worker's disk (slow, high RAM/Disk usage), we use **Presigned S3 URLs**. FFmpeg streams the video directly from S3 over the network (`https://...`).

### Step-by-Step Workflow

1.  **Probing:**
    The processor inspects video metadata (resolution, duration, rotation) without downloading the file.
    * *Example:* Input is `1920x1080` (1080p), Duration is `60s`.

2.  **Thumbnail Generation (0-5% Progress):**
    * Extracts a frame at `t=1s` (avoids black frames at start).
    * Uploads it to S3 (`processed/{id}/thumbnail.jpg`).
    * **Callback:** Immediately notifies the Task "Thumbnail is ready!" so the UI can show a preview image.

3.  **Smart Resolution Logic:**
    Calculates HLS variants based on input size. It **never upscales**.
    * Input 1080p → Generates 1080p, 720p, 480p, 360p, 240p.
    * Input 480p → Generates 480p, 360p, 240p.

4.  **HLS Transcoding (5-100% Progress):**
    Loops through each target resolution.
    * Starts FFmpeg to slice the stream into `.ts` chunks (10s each).
    * While FFmpeg runs, it reports raw time data to a local TCP socket.
    * Uploads chunks and `.m3u8` playlists to S3.

5.  **Cleanup:**
    * Deletes the original raw video from S3 to save storage costs.
    * Cleans up local temporary files.

---

## 2. FFmpeg Progress Tracker (`ffmpeg_progress.py`)

FFmpeg does not output a simple percentage. It outputs "time processed" (e.g., "I have processed 00:00:15"). We must calculate the percentage manually.

### How it works:
1.  **Socket Server:** The Python class opens a temporary TCP server on a random port (e.g., `localhost:45123`).
2.  **FFmpeg Hook:** We pass the argument `-progress tcp://127.0.0.1:45123` to FFmpeg.
3.  **Math Logic:**
    * FFmpeg sends: `out_time_ms=15000000` (15 seconds).
    * Total Video Duration: `60 seconds`.
    * Calculation: `(15 / 60) * 100 = 25%`.
4.  **Weighted Progress:**
    Since we run FFmpeg multiple times (once per resolution), the tracker scales the percentage.
    * If we have 4 resolutions, finishing one resolution adds ~23.75% to the *Global Progress*.

---

## 3. The Celery Task (`tasks.py`)

The Task acts as a **Throttler**. Sending a WebSocket update for every millisecond of progress would crash the frontend and database.

### The "Smart Notification" Logic

| Action | Frequency | Purpose |
| :--- | :--- | :--- |
| **WebSocket Update** | Every **2%** change | Keeps the UI circle loader smooth and responsive. |
| **Database Update** | Every **10%** change | Persists state in case of restart, reduces SQL write load. |
| **Thumbnail Event** | **Immediate** | Sent the moment the JPG is ready. |

### Logic Flow

```python
def on_progress(percent, thumb_key=None):
    # 1. Immediate UI Feedback (Thumbnail)
    if thumb_key:
        save_thumbnail(thumb_key)
        notify_ui(stage="thumbnail_ready")

    # 2. WebSocket Throttling (2%)
    if percent - last_ws_update >= 2:
        notify_ui(progress=percent)
        last_ws_update = percent

    # 3. Database Throttling (10%)
    if percent - last_db_update >= 10:
        update_db(progress=percent)
        last_db_update = percent

```








# Video Processing & HLS Workflow

## Overview
This document outlines the architecture for processing video uploads in Pulse Chat. The system transforms raw, large video files into optimized **HLS (HTTP Live Streaming)** playlists with adaptive bitrates. It features **Real-Time Progress Tracking** for UI feedback and **Fault Tolerance** to resume processing after server crashes.

## 1. The Video Processor (`video_processor.py`)

### Strategy: Zero-Download Streaming
Instead of downloading a 1GB file to the worker's disk (which is slow and uses high RAM/Disk), we use **Presigned S3 URLs**. FFmpeg streams the video directly from S3 over the network (`https://...`), keeping disk usage minimal.

### Workflow Steps

1.  **Probing:**
    The processor inspects video metadata (resolution, duration) without downloading the file.

2.  **Thumbnail Generation (0-5%):**
    * Extracts a frame at `t=1s`.
    * **Callback:** Immediately notifies the Task "Thumbnail is ready!" so the UI can show a preview image.

3.  **Smart Resolution Logic:**
    Calculates HLS variants based on input size (Never upscales).
    * Input 1080p → Generates 1080p, 720p, 480p, 360p, 240p.
    * Input 480p → Generates 480p, 360p, 240p.

4.  **HLS Transcoding (5-100%):**
    Loops through each target resolution:
    * **Checkpoint Check:** Checks the database (`hls_parts`) to see if this resolution was already finished (in case of a previous crash). If yes, it skips transcoding.
    * **Transcode:** Runs FFmpeg to slice the stream into `.ts` chunks.
    * **Progress Tracking:** FFmpeg reports raw time data to a local TCP socket.
    * **Save Checkpoint:** After uploading chunks, updates the DB to mark this resolution as "Done".

5.  **Cleanup:**
    * Deletes the original raw video from S3.
    * Cleans up local temporary files.

---

## 2. Progress Tracking & Throttling

### FFmpeg Progress Tracker
A TCP socket listener (`ffmpeg_progress.py`) captures raw time data from FFmpeg (e.g., "00:00:15 processed") and converts it to a percentage (e.g., "25%").

### Notification Throttling (Celery Task)
To prevent crashing the frontend or database with too many updates:

| Action | Frequency | Purpose |
| :--- | :--- | :--- |
| **WebSocket Update** | Every **2%** | Keeps the UI circle loader smooth. |
| **Database Update** | Every **10%** | Persists state, reduces SQL write load. |
| **Thumbnail Event** | **Immediate** | Sent the moment the JPG is ready. |

---

## 3. Fault Tolerance (Resume Capability)

If the server crashes (e.g., OOM Kill, Power Outage) while processing `720p`:
1.  **Celery Retry:** The task is re-queued automatically (`acks_late=True`).
2.  **State Check:** The worker starts, loads the asset, and sees `variants['hls_parts'] = {'1080p': True}`.
3.  **Resume:** It **skips** 1080p (saving ~5 mins) and starts immediately at 720p.