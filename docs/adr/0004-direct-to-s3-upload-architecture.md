# ADR-0004: Direct-to-Cloud (S3) Upload Architecture

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** CHAT-002, USR-004, MEDIA-001, MEDIA-002

## Context
Handling binary file uploads (multi-gigabyte video, images, PDFs) directly on Django web application servers saturates web worker processes, consumes large amounts of memory buffers, blocks I/O event loops, and makes autoscaling web tiers costly and ineffective.

## Decision
Pulse Chat employs a **Direct-to-S3 Presigned Upload** model:
1. The client sends only file metadata (`file_name`, `file_size`, `content_type`, `kind`) to the Django REST endpoint.
2. The server responds with presigned AWS S3 URLs:
   - For files $\le$ 5 MB: A single presigned `PUT` URL (`mode: "direct"`).
   - For files $>$ 5 MB: A multipart upload ID and an initial batch of presigned `upload_part` URLs (`mode: "multipart"`).
3. The client uploads raw binary data directly to S3.
4. Upon upload completion, the client invokes `POST /api/chat/complete/`, causing Django to notify asynchronous Celery workers to process the object directly from S3.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Upload through Django (`MultiPartParser`) | Simple single HTTP request | Exhausts server memory; ties up Gunicorn workers; poor network resilience for large files. |
| Dedicated upload microservice | Offloads main API | Additional deployment, networking, and maintenance complexity. |

## Consequences
**Positive**
- Django servers handle lightweight JSON metadata only; zero binary payload overhead.
- Direct multipart uploads provide client-side retryability for failed chunks on weak mobile connections.
- S3 handles bandwidth scaling and SSL termination for large file streams.

**Rules this creates**
- Application views must never accept raw binary upload streams in `request.FILES` for message attachments.
- S3 operations must use the centralized `utils/s3.py` helper functions.
