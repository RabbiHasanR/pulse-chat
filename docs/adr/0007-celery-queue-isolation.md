# ADR-0007: Celery Queue Isolation and CPU-Bounded Video Workers

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** MEDIA-001, MEDIA-002, MEDIA-003, MEDIA-004, MEDIA-005

## Context
Background tasks in Pulse Chat range from fast I/O operations (dispatching transactional emails, updating delivery receipts) to medium-weight image resizing, up to compute-intensive, long-running HLS video transcoding jobs running multi-threaded FFmpeg pipelines. If all tasks share a single global Celery queue, heavy video jobs will monopolize worker processes, causing high latency or complete starvation of critical real-time notifications.

## Decision
We enforce strict queue isolation across distinct worker processes configured in `docker-compose.yml`:
1. `video_queue` (Worker: `celery_video`): Dedicated strictly to FFmpeg video transcoding jobs (`process_video_task`). Worker concurrency is constrained to 2 to prevent CPU and RAM exhaustion.
2. `image_queue`, `audio_queue`, `file_queue` (Worker: `celery_media`): Concurrency 4. Dedicated to media transformation tasks.
3. `default` (Worker: `celery_default`): Concurrency 4. Dedicated to lightweight tasks, emails, delivery reports, and periodic sweepers.
4. All tasks must specify their queue routing in `background_worker/celery.py` `task_routes`.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Single shared queue (`celery`) | Simple configuration | Video transcoding tasks block transactional emails and receipt notifications. |
| Serverless encoding (AWS MediaConvert) | Zero server management | Incompatible with local offline development (Moto mock mode) and increases AWS costs. |

## Consequences
**Positive**
- High-priority operational and notification tasks execute with zero queuing delay.
- Heavy transcoding jobs cannot exhaust server host resources.

**Rules this creates**
- Every new Celery task must explicitly declare its queue in `task_routes`.
- Heavy FFmpeg operations must never be dispatched to the `default` queue.
