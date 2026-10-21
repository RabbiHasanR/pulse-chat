---
id: MEDIA-005
title: Asset Finalization, Failure Routing & Stuck Sweeping
status: implemented
area: media
owners: [rabbihasan]
code:
  - server/background_worker/chats/tasks.py
  - server/background_worker/celery.py
contracts:
  - docs/contracts/websocket-events.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0007-celery-queue-isolation.md
last_verified: 2026-10-21
---

# MEDIA-005 — Asset Finalization, Failure Routing & Stuck Sweeping

## 1. Summary
Provides the shared lifecycle conclusion for all asynchronous media processing tasks: `_finalize_asset` commits output variants to PostgreSQL atomically and broadcasts updates to the sender and viewing receiver; `_handle_failure` marks assets failed; and periodic Celery Beat sweeper `cleanup_stuck_assets` cleans up abandoned and crashed jobs.

## 2. Context
Asynchronous media processing can succeed, crash due to network disconnects, or be abandoned mid-upload by clients. A unified finalizer ensures consistent message status transitions and socket pushes across all media kinds, while background sweepers prevent orphan tasks from remaining in `running` status forever.

## 3. Scope
**In scope**
- Atomic database commit of asset output metadata (`object_key`, dimensions, variants, status).
- Determining parent `ChatMessage` status based on recipient viewing/online status.
- Smart socket routing: pushing `chat_message_update` unconditionally to sender, but to receiver strictly if actively viewing the chat.
- Graceful failure handling via `_handle_failure()` marking asset failed and updating parent message status to `failed` if all assets failed.
- Periodic Celery Beat hourly task `cleanup_stuck_assets` failing assets queued $> 24$h or running $> 2$h.

**Out of scope**
- S3 bucket lifecycle policies (e.g. S3 expiration of unconfirmed multipart uploads).

## 4. Behavior
- **Success (`_finalize_asset`)**:
  1. Writes variants, dimensions, and `processing_status="done"` to `MediaAsset`.
  2. If receiver is viewing chat, message status becomes `seen`. If online, `delivered`.
  3. Pushes `chat_message_update` via `_send_socket_update_directly` to `user_{sender}`.
  4. If receiver is currently viewing the chat, pushes `chat_message_update` to `user_{receiver}`.
  5. If status transitioned to `seen`, pushes `chat_read_receipt` to sender.
- **Failure (`_handle_failure`)**:
  1. Sets `asset.processing_status = 'failed'` and stores error log in `variants['error_log']`.
  2. If message has no text and all assets failed, message status is set to `failed`.
  3. Pushes `chat_message_update` failure event over WebSocket.
- **Hourly Sweeper (`cleanup_stuck_assets`)**:
  1. Queries assets where `processing_status='queued'` and `created_at <= now - 24h`, or `processing_status='running'` and `created_at <= now - 2h`.
  2. Updates them to `failed`.

## 5. Interface
- **WebSocket Broadcast**:
  ```json
  {
    "type": "chat_message_update",
    "success": true,
    "data": {
      "id": 501,
      "conversation_id": 24,
      "status": "delivered",
      "media_assets": [
        {
          "id": 89,
          "kind": "image",
          "processing_status": "done",
          "url": "https://...",
          "thumbnail_url": "https://...",
          "file_size": 150000,
          "width": 1920,
          "height": 1080,
          "duration_seconds": null
        }
      ]
    }
  }
  ```

## 6. Acceptance criteria
- **AC1** When `_finalize_asset` completes, `MediaAsset.processing_status` is updated to `"done"` in an atomic transaction.
- **AC2** If the receiver has the chat open (`viewing` set $> 0$), message status updates to `"seen"`, and a `chat_read_receipt` is dispatched to the sender.
- **AC3** `chat_message_update` is always sent to the sender's channel group.
- **AC4** `chat_message_update` is sent to the receiver's channel group only if the receiver is currently viewing the chat.
- **AC5** When a processing exception occurs, `_handle_failure` records the error string in `asset.variants['error_log']` and sets status `"failed"`.
- **AC6** If all assets in a message fail and the message has no text content, the parent message status transitions to `"failed"`.
- **AC7** `cleanup_stuck_assets` identifies queued assets older than 24h and running assets older than 2h, transitions them to `"failed"`, and pushes socket updates.

## 7. Non-functional requirements
- Socket notifications are pushed directly to the Channels layer bypassing Celery task queues.

## 8. Known deviations
- BUG-003: `app.conf.beat_schedule` in `background_worker/celery.py` registers the task as `'chats.tasks.cleanup_stuck_assets'` instead of `'background_worker.chats.tasks.cleanup_stuck_assets'`.
- BUG-012: Task sets `asset.processing_progress = 100.0`, referencing a field that was removed in migration 0007.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
