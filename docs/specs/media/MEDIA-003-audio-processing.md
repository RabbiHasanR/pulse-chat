---
id: MEDIA-003
title: Asynchronous Audio Transcoding & Waveform Extraction
status: implemented
area: media
owners: [rabbihasan]
code:
  - server/utils/media_processors/audio.py
  - server/background_worker/chats/tasks.py
contracts:
  - docs/contracts/websocket-events.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0007-celery-queue-isolation.md
last_verified: 2026-10-21
---

# MEDIA-003 — Asynchronous Audio Transcoding & Waveform Extraction

## 1. Summary
Processes uploaded voice notes and audio clips in Celery (`audio_queue`): runs concurrent magic byte and duration probes, executes a single-pass FFmpeg split pipeline converting audio to 64k AAC/M4A while branching a PCM audio stream to compute 50 visual waveform peak bars via numpy, uploads the asset to S3, and removes the original raw upload.

## 2. Context
Chat voice messages require standard playback formats (M4A/AAC) and pre-calculated visual waveform data so client UI players can immediately render interactive voice bubble bars.

## 3. Scope
**In scope**
- Concurrent magic-byte security check and FFprobe duration inspection (`ThreadPoolExecutor`).
- Single-pass FFmpeg transcode into AAC/M4A (`movflags=+faststart`).
- Branching PCM audio (`s16le`, 8kHz) to compute 50 waveform peak bar values.
- Memory/file strategy: in-memory pipe for audio $\le$ 20 minutes (1200s); temp PCM file for longer audio.
- Deleting raw upload object from S3.

**Out of scope**
- Speech-to-text transcription.

## 4. Behavior
When `process_audio_task` runs on `celery_media`:
1. `AudioProcessor` generates a presigned S3 GET URL.
2. Runs parallel MIME inspection and duration probing.
3. FFmpeg decodes stream once and splits:
   - Branch A: Encodes AAC M4A file to disk (`movflags=+faststart`).
   - Branch B: Streams 8kHz mono PCM to pipe or temp file.
4. Numpy calculates 50 peak amplitude bars (range 0 to 100).
5. Uploads `audio.m4a` to S3 and calls `_finalize_asset()`.

## 5. Interface
- **Processor Output**:
  ```python
  {
      "object_key": "processed/94/audio.m4a",
      "file_size": 95400,
      "duration_seconds": 12.4,
      "variants": {
          "type": "audio",
          "format": "m4a",
          "waveform": [12, 45, 78, 60, 30, ... 50 integer peak bars ...]
      }
  }
  ```

## 6. Acceptance criteria
- **AC1** Task executes exclusively on Celery queue `audio_queue`.
- **AC2** If the file contains no valid audio stream, processing aborts with an error and deletes the raw file from S3.
- **AC3** Audio is converted to mono AAC format with bitrate 64k and `+faststart` atom flags for immediate web streaming.
- **AC4** Processed variants metadata contains a `waveform` key containing exactly 50 normalized integer values representing audio amplitude peaks.
- **AC5** If audio duration is $\le$ 1200 seconds, PCM data is piped in memory; if $> 1200$ seconds, it spools to a temp PCM file.
- **AC6** The raw unoptimized upload is deleted from S3 upon successful completion.

## 7. Non-functional requirements
- Max retries: 3. Retry backoff: 10 seconds. S3 cache headers set `CacheControl: max-age=31536000`.

## 8. Known deviations
- None.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
