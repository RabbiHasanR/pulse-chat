---
name: review-celery-task
description: >-
  Audit Celery task configurations in Pulse Chat — queue routing, worker isolation,
  retry policies, soft/hard time limits, acks_late, and failure handling.
---

# review-celery-task — Pulse Chat Celery Task Audit

Audit Celery task definitions and routing configurations against Pulse Chat production standards.

## Audit Checklist

1. **Task Decorator Configuration** — for every `@shared_task` or `@app.task`:
   - `bind=True`: Required for tasks that implement retries or access task request state.
   - `acks_late=True`: Required to prevent message loss on worker crash or OOM kills.
   - `max_retries`: Must be explicitly set (never leave unlimited/None).
   - `soft_time_limit`: Must be set (allows task to catch `SoftTimeLimitExceeded` for cleanup).
   - `time_limit`: Must be set and strictly greater than `soft_time_limit` (hard kill margin).
   - `queue`: Explicit queue name or verified routing entry in `task_routes`.

2. **Queue Routing (`background_worker/celery.py`)**:
   - Every task must be mapped in `task_routes`.
   - Dedicated queues:
     - `default`: Lightweight general tasks (emails, notifications).
     - `video_queue`: FFmpeg transcoding / video processing (isolated worker, high CPU/RAM).
     - `image_queue`: Pillow image optimization / thumbnail generation.
     - `audio_queue`: Audio waveform generation / conversion.
     - `file_queue`: Document preview / virus scanning.
   - Heavy tasks (video, audio) MUST NEVER route to `default`.

3. **Error Handling & Failure Recovery**:
   - Tasks modifying media models (`Asset`) must update status (e.g. `asset.processing_status = 'failed'`) in `try...except` or task `on_failure`.
   - When failure occurs, dispatch real-time failure notification to client via WebSocket (`ChatRedisService` or designated failure handler).
   - Exceptions must be logged; never swallow exceptions without error-state updates.

4. **Task Argument Serialization**:
   - Pass primitive identifiers (integer IDs, UUID strings), never Django model instances.
   - Reload model from database inside the task execution to ensure fresh state.

## Output Format

Present tasks in a summary table:
| Task Name | Queue | Bind | Acks Late | Max Retries | Soft Limit | Hard Limit | Status |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|

Follow with specific line-level findings:
- `file:line` — **[Severity]** issue description — recommended fix.

Conclude with final verdict: **PASS** or **FAIL**.
