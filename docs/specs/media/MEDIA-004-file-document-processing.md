---
id: MEDIA-004
title: File & Document Attachment Processing
status: implemented
area: media
owners: [rabbihasan]
code:
  - server/utils/media_processors/file.py
  - server/background_worker/chats/tasks.py
contracts:
  - docs/contracts/websocket-events.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0007-celery-queue-isolation.md
last_verified: 2026-10-21
---

# MEDIA-004 — File & Document Attachment Processing

## 1. Summary
Processes arbitrary document attachments (PDFs, text files, spreadsheets) in Celery (`file_queue`). Performs security checks on the initial 2KB without downloading the full document, and for PDFs generates a first-page JPEG cover preview thumbnail and page count.

## 2. Context
Users attach documents up to large sizes. Rather than downloading arbitrary huge binaries onto workers, processing inspects file headers remotely, blocks dangerous executables, and produces visual card previews for PDF documents.

## 3. Scope
**In scope**
- Sniffing MIME type from the first 2KB of S3 object range request (`libmagic`).
- Blocking forbidden executable and script MIME types (`application/x-dosexec`, `application/x-executable`, `application/x-sh`, `text/x-python`, `text/javascript`, `text/html`).
- Extracting exact file size from S3 `ContentRange` header.
- For PDFs under 50 MB: rendering page 1 at 75 DPI to create a 500px JPEG thumbnail and extracting page count via `pdfinfo`.
- Idempotency check: skipping re-rendering if thumbnail already exists in S3.

**Out of scope**
- Full-text document OCR or search indexing.

## 4. Behavior
When `process_file_task` runs on `celery_media`:
1. `FileProcessor` fetches the first 2048 bytes from S3.
2. Checks magic bytes; aborts with error if forbidden.
3. If MIME type is `application/pdf` and file $\le$ 50 MB:
   - Downloads document to lazy temporary directory.
   - Extracts page count via `pdfinfo`.
   - Renders page 1 at 75 DPI, scales to 500px, uploads thumbnail to S3.
4. Returns metadata dict with MIME type, size, and preview info to `_finalize_asset()`.

## 5. Interface
- **Processor Output**:
  ```python
  {
      "object_key": "raw_uploads/.../report.pdf",
      "file_size": 1542000,
      "variants": {
          "type": "file",
          "mime_type": "application/pdf",
          "file_size": 1542000,
          "is_preview_available": True,
          "thumbnail": "processed/98/thumbnail.jpg",
          "page_count": 14
      }
  }
  ```

## 6. Acceptance criteria
- **AC1** Task executes exclusively on Celery queue `file_queue`.
- **AC2** Dangerous executable and script file types (`.exe`, `.sh`, `.py`, `.js`, `.html`) are detected and rejected via magic bytes inspection.
- **AC3** For valid PDF documents under 50 MB, page 1 is rendered into a thumbnail with maximum dimension 500px and uploaded to S3.
- **AC4** For PDF documents exceeding 50 MB, preview rendering is skipped and marked `"error": "File too large for preview generation"`.
- **AC5** Non-PDF files have their verified MIME type recorded with `is_preview_available = False`.
- **AC6** Temporary directories are cleaned up immediately in a `finally` block.

## 7. Non-functional requirements
- Pillow decompression bomb protection: max 50 megapixels. Max download size: 50 MB.

## 8. Known deviations
- None.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
