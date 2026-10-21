---
id: MEDIA-001
title: Asynchronous Image Processing Pipeline
status: implemented
area: media
owners: [rabbihasan]
code:
  - server/utils/media_processors/image.py
  - server/background_worker/chats/tasks.py
contracts:
  - docs/contracts/websocket-events.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0007-celery-queue-isolation.md
last_verified: 2026-10-21
---

# MEDIA-001 — Asynchronous Image Processing Pipeline

## 1. Summary
Processes raw uploaded images in Celery (`image_queue`): validates file magic bytes, protects against decompression bombs, applies EXIF orientation fixes, resizes to a max 1920px boundary, generates a 300px WebP thumbnail, uploads processed WebP assets to S3, and removes the raw upload.

## 2. Context
User photos taken on modern mobile devices range from 5MB to 25MB and can exceed 48 megapixels. Streaming raw photos directly to client devices causes severe bandwidth waste and UI rendering lag. Image processing optimizes images into modern WebP format with small dimensions and generates instant thumbnails.

## 3. Scope
**In scope**
- Remote validation of first 2KB for valid image MIME types (`libmagic`).
- Enforcing max file size limit (25 MB) and max pixel count (Pillow decompression bomb protection).
- Correcting EXIF rotation (`ImageOps.exif_transpose`).
- Downscaling optimized variant to max 1920px (quality 80 WebP).
- Downscaling thumbnail variant to max 300px.
- Uploading WebP variants to S3 (`processed/{asset.id}/...`).
- Deleting raw upload object from S3.

**Out of scope**
- Animated GIF/WebP conversion (treated as static image or video).

## 4. Behavior
When `process_image_task` executes on worker `celery_media`:
1. `ImageProcessor.process()` fetches metadata and inspects remote header.
2. Streams raw file into memory (safe under 25MB).
3. Opens Pillow image, verifies integrity, transposes EXIF orientation.
4. Generates optimized WebP and thumbnail WebP.
5. Uploads both variants to S3 and returns metadata dict with keys, dimensions, and file size.
6. Calls `_finalize_asset()`.

## 5. Interface
- **Processor Output**:
  ```python
  {
      "object_key": "processed/89/uuid_optimized.webp",
      "content_type": "image/webp",
      "file_size": 184500,
      "width": 1920,
      "height": 1080,
      "variants": {
          "type": "image",
          "thumbnail": "processed/89/uuid_thumb.webp",
          "original_width": 4032,
          "original_height": 3024
      }
  }
  ```

## 6. Acceptance criteria
- **AC1** When `process_image_task` runs, `MediaAsset` processing status transitions to `running`.
- **AC2** If the raw file is not an allowed image MIME type (`jpeg`, `png`, `webp`, `gif`, `bmp`, `tiff`) or exceeds 25 MB, processing aborts with an error and deletes the raw file.
- **AC3** Decompression bomb images (> 89,478,485 pixels) are rejected immediately.
- **AC4** Processed output contains an optimized WebP image capped at 1920px on the longest dimension, and a thumbnail capped at 300px.
- **AC5** Upon successful processing, the raw upload file is deleted from S3.
- **AC6** `_finalize_asset` updates `MediaAsset` to status `"done"`, populates `variants`, and dispatches `chat_message_update` over WebSocket.

## 7. Non-functional requirements
- Executes exclusively on Celery queue `image_queue`.
- S3 cache headers set `CacheControl: max-age=31536000` for immutable processed variants.

## 8. Known deviations
- BUG-007: `MediaAsset.url` ignores `USE_S3_MOCK` and assumes real AWS URL.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
