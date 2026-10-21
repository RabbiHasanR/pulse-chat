# 📘 Pulse Chat Video Processing Architecture

## **1. System Overview**

This system transforms raw, large video uploads into **Adaptive HLS Streams** (like Netflix/YouTube). It is designed for **High Availability**, **Resilience** (Crash Recovery), and **Instant User Feedback** (Progressive Playback).

### **The Core Philosophy**

1. **Zero-Download Transcoding:** We stream input directly from S3 to FFmpeg to avoid filling worker disk space.
2. **Progressive Availability:** The user can start watching (at 240p) while the HD versions process in the background.
3. **Stateless Resumability:** If a worker crashes, the next worker picks up exactly where the last one left off.
4. **Resilient "Partial Success":** If a job fails halfway (e.g., after 480p), the system marks it as `partial` instead of `failed`, allowing users to watch the lower-quality version while flagging it for admin review.

---

## **2. Workflow Diagram**

1. **Client:** Completes S3 Upload → Calls API.
2. **API:** Validates → Dispatches Celery Task (Async).
3. **Worker:** Tracks Progress → Generates Thumbnail → Transcodes HLS.
4. **Sockets:** Stream updates to Frontend (2% intervals).s
5. **Player:** Loads `master.m3u8` as soon as the first quality is ready.

---

## **3. Component Breakdown**

### **A. The Trigger: `CompleteUpload` View**

**File:** `views.py`
**Role:** The Handoff. It acknowledges the upload and passes control to the background worker.

* **S3 Multipart Completion:**
* Code: `s3.complete_multipart_upload(...)`
* *Why:* Large files are uploaded in chunks. This command stitches them together on AWS S3.


* **Asset Status:**
* Code: `asset.processing_status = "queued"`
* *Why:* Ensures the asset is marked as ready for processing but prevents race conditions if the user hits the endpoint twice.


* **Immediate Feedback:**
* Code: `notify_message_event.delay(..., stage="processing")`
* *Why:* The UI immediately replaces the "Uploading..." bar with a "Processing..." spinner.


* **Task Routing:**
* Code: `if kind == VIDEO: process_video_task.delay(asset.id)`
* *Why:* Routes heavy video tasks to a dedicated `video_queue` (high CPU workers), keeping light tasks (images) separate.



### **B. The Orchestrator: `process_video_task**`

**File:** `tasks.py`
**Role:** Project Manager. It manages the lifecycle, handles errors, and throttles updates.

1. **Progress Throttling (The "Anti-Spam" Logic):**
* **Problem:** FFmpeg updates 10 times a second. Sending 10 WebSockets/sec would crash the frontend.
* **Solution:**
```python
if abs(percent - last_ws_progress) >= 2:
    notify_message_event.delay(...)

```


* **Result:** UI updates smoothly every 2%, reducing network load by 90%.


2. **The "Playable" Callback (The Netflix Trick):**
* **Code:** `on_playable(master_key)`
* **Logic:** Triggered as soon as **240p** is done.
* **Action:**
1. Updates DB `object_key` to point to the `.m3u8` playlist.
2. Sends `stage: "playable"` to UI.


* **Result:** The "Play" button appears after ~15 seconds, even if the 1080p job takes 5 minutes.


3. **Resumable Checkpoints:**
* **Code:** `on_checkpoint(variant_name)`
* **Logic:** Saves `hls_parts: {'240p': True}` to the DB.
* **Why:** If the worker crashes (OOM) during 720p, the retry task checks DB, skips 240p, and resumes 720p immediately.


4. **Smart Retries & Partial Success:**
* **Retry Logic:** Retries on Network errors (S3 down) with exponential backoff.
* **Partial Success Handler:**
* If `max_retries` is hit or a logic error occurs:
* **Check:** Did we successfully generate *any* playable variant (e.g., 240p)?
* **Yes:** Set status to `partial`. The user can still watch the video.
* **No:** Set status to `failed`. Show error icon.





### **C. The Engine: `VideoProcessor**`

**File:** `video_processor.py`
**Role:** The Factory. Executes the FFmpeg commands.

1. **Isolation (`tempfile.mkdtemp`):**
* Creates a unique folder `/tmp/job_xyz/` for every task.
* *Why:* Prevents file collisions if multiple workers run on the same server.


2. **Input Streaming (`get_input_url`):**
* Generates a signed S3 URL.
* *Why:* FFmpeg reads from `https://s3...` directly. We **do not download** the 2GB raw file to disk.


3. **WebP Thumbnails:**
* **Code:** `vcodec='libwebp', qscale=75`
* *Why:* WebP is 30% smaller than JPEG. `qscale=75` is the visual sweet spot.


4. **The "Smallest First" Strategy:**
* **Code:** `valid_resolutions.sort(key=lambda x: x['h'])`
* **Logic:** Processes **240p → 1080p**.
* *Why:* Essential for "Progressive Playback". If we did 1080p first, the user would wait minutes to watch anything.


5. **Clean-As-You-Go:**
* **Code:** `shutil.rmtree(variant_dir)` inside the loop.
* *Why:* Immediately deletes the 500MB of `.ts` files after upload. Keeps local disk usage low (<1GB) even for 4K videos.


6. **Smart Caching (New):**
* **Code:** Checks if the current variant is the *last* one.
* **Logic:**
* **Intermediate Uploads:** `Cache-Control: no-cache` (File is still growing).
* **Final Upload (100%):** `Cache-Control: max-age=31536000` (File is immutable).


* **Why:** Saves bandwidth costs. Once a video is fully processed, clients cache the playlist for 1 year.



### **D. The Translator: `FFmpegProgressTracker**`

**File:** `ffmpeg_progress.py`
**Role:** The Interpreter. Bridges the gap between C++ (FFmpeg) and Python.

1. **TCP Socket Server:**
* **Code:** `socket.bind(('localhost', 0))`
* *Why:* Parsing terminal output (`stdout`) is brittle. FFmpeg has a native mode (`-progress tcp://...`) to send machine-readable data to a port.


2. **Time-to-Percentage Math:**
* **Input:** `out_time_us=15000000` (15 seconds processed).
* **Math:** `(15s / Total Duration) * 100`.
* **Output:** Calls `on_progress(25.0)`.



---

## **4. Data Flow & Notification Events**

Here is the exact sequence of events the Frontend Client receives via WebSocket.

| Stage | Event Payload (Simplified) | Frontend Action |
| --- | --- | --- |
| **1. Upload** | *(API Response 200 OK)* | Show "Processing..." spinner. |
| **2. Thumbnail** | `type: "update", stage: "thumbnail_ready", url: "thumb.webp"` | Replace spinner with blurred thumbnail image. |
| **3. Processing** | `type: "update", progress: 12.5` | Update circular progress bar to 12.5%. |
| **4. Playable** | `type: "update", stage: "playable", url: "master.m3u8"` | **Enable Play Button.** Video plays in Low Quality. |
| **5. Processing** | `type: "update", progress: 45.0` | Update progress bar (Running in background). |
| **6. Done** | `type: "update", stage: "done", progress: 100` | Remove progress bar. Video is now Full HD. |
| **7. Partial** | `type: "update", stage: "failed", status: "partial"` | Hide progress bar. Keep Play button enabled (Playable but low-res). |

---

## **5. Storage Structure (S3)**

How the files are organized in the bucket:

```text
processed/{asset_id}/
├── thumbnail.webp        (Cache: 1 Year)
└── hls/
    ├── master.m3u8       (Cache Logic: "no-cache" until done, then "1 Year")
    ├── 240p/
    │   ├── index.m3u8
    │   ├── seg_000.ts    (Cache: 1 Year)
    │   └── seg_001.ts
    └── 1080p/
        ├── index.m3u8
        └── ...

```

* **`master.m3u8`**: The "Menu". Lists available qualities.
* **During Processing / Partial Failure:** `Cache-Control: no-cache`. Clients must check for updates/fixes.
* **After Success:** `Cache-Control: max-age=31536000`. Clients cache it forever.


* **`.ts` segments**: The actual video chunks. Immutable and always cached for 1 year.

---

## **6. Failure Recovery Strategy**

What happens if the server explodes?

| Failure Type | Handling Strategy | S3 Cache State |
| --- | --- | --- |
| **Network Error (S3)** | `process_video_task` catches `BotoCoreError`. **Exponential Backoff** retry (10s, 20s, 40s). | `no-cache` |
| **Worker Crash (OOM)** | Celery `acks_late=True` requeues task. New worker checks DB `hls_parts`, skips finished resolutions, and finishes the job. | `no-cache` |
| **Logic Error (Bug)** | Catch `Exception`. **Check if playable.** <br>

<br> - If Yes: Mark `partial`. User watches low-res. <br>

<br> - If No: Mark `failed`. User sees error. | `no-cache` (Allows future retry/fix) |
| **Success** | All resolutions processed. DB Status: `done`. | `max-age=1 Year` |

---

This documentation covers the full lifecycle of your video processing pipeline, explaining not just *what* the code does, but *why* it is architected this way for a production environment.