---
name: add-celery-task
description: Guided workflow for adding a new Celery task to Pulse Chat — correct queue, required config, retry logic, task_routes registration, WebSocket notification.
---

Add a new Celery task. Follow steps in order. Wait for approval at each step.

$ARGUMENTS: what the task does (e.g. "send push notification", "resize avatar image")

## Step 1 — Clarify
- What triggers it? (view / beat schedule / another task)
- Which queue?
  - `video_queue` — FFmpeg only
  - `image_queue` / `audio_queue` / `file_queue` — media processing
  - `default` — everything else
- Retry logic needed? (almost always yes)
- WebSocket progress updates needed?

## Step 2 — File Placement
- User tasks → `background_worker/users/tasks.py`
- Chat/media tasks → `background_worker/chats/tasks.py`
Read the existing file to match patterns.

## Step 3 — Write Task
Required config — no exceptions:
```python
@shared_task(
    bind=True,
    acks_late=True,
    max_retries=3,
    soft_time_limit=<N>,
    time_limit=<N+60>,
    queue='<queue>'
)
def my_task(self, resource_id: int) -> None:
```
- Pass IDs, not objects (JSON serialization)
- try/except → `self.retry(exc=exc)`
- Update model status at start and end

Show, wait for approval.

## Step 4 — Register in task_routes
Add to `background_worker/celery.py`:
```python
'background_worker.<module>.tasks.<name>': {'queue': '<queue>'}
```
Show, wait for approval.

## Step 5 — WebSocket notification (if needed)
- Progress: `_send_socket_update_directly()` (non-blocking)
- Final status: `_finalize_asset()` pattern
Read `background_worker/chats/tasks.py` for the pattern.

## Step 6 — Test
File: `tests/background_worker/test_chat_tasks.py` or `test_user_tasks.py`.
Fixtures: `patch_global_s3_client` + `fake_redis`.
Show, wait for approval.

## Step 7 — Validate
Run `celery-task-auditor` agent on the new task file.
Run `/test tests/background_worker/` to confirm tests pass.
