---
name: add-celery-task
description: >-
  Guided workflow for adding a new Celery task to Pulse Chat — verifies the
  approved specification, correct queue routing, required decorator kwargs,
  time limits, serialization restrictions, and task_routes registration.
---

# add-celery-task — Add a New Background Celery Task

**Input:** Task description or spec ID (e.g. "generate video preview gif", "send push notification").

## Step 0 — Spec Check
1. Ensure the background task is defined in an `approved` spec under `docs/specs/media/`, `docs/specs/auth/`, etc.
2. If none exists, invoke `spec-write` first.

## Step 1 — Select Target Queue
Select the dedicated queue based on the processing profile:
- `video_queue`: Dedicated to FFmpeg video transcoding jobs.
- `image_queue`: Image resizing and WebP conversion.
- `audio_queue`: Audio transcoding and waveform peak calculation.
- `file_queue`: PDF rendering and document parsing.
- `default`: Low-latency notifications, transactional emails, database cleanups.

## Step 2 — File Placement
- User-related background tasks $\to$ `server/background_worker/users/tasks.py`.
- Chat and media processing tasks $\to$ `server/background_worker/chats/tasks.py`.

## Step 3 — Task Definition & Decorator Config
Decorate the task function with `@shared_task` using all mandatory kwargs:
```python
@shared_task(
    bind=True,
    acks_late=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
    queue='<queue_name>'
)
def my_task(self, resource_id: int):
    # Pass IDs only (integers), never model instances!
```

## Step 4 — Register in `task_routes`
Open `server/background_worker/celery.py` and register the task in `app.conf.task_routes`:
```python
app.conf.task_routes = {
    ...
    'background_worker.<module>.tasks.<task_name>': {'queue': '<queue_name>'},
}
```

## Step 5 — Audit Configuration
Run the Celery task auditor skill to verify compliance:
Invoke `review-celery-task` on the modified file.
