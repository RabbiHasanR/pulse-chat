# Known Issues

Bugs, security gaps, and spec deviations found in the current code. Each issue is linked from the spec(s) it violates (*Known deviations* section). In the TDD phase, each open issue becomes a failing test first, then a fix.

**Severity:** 🔴 critical/high · 🟠 medium · 🟡 low · ⚪ code smell
**Status:** `open` → `in-progress` → `fixed` (keep fixed entries for history, with the fixing commit)

| ID | Sev | Title | Specs | Status |
| --- | --- | --- | --- | --- |
| BUG-001 | 🔴 | `send-otp` returns the OTP in the response body | AUTH-002 | open |
| BUG-002 | 🔴 | Tasks without explicit queue go to unconsumed `celery` queue | RT-003, AUTH-001, AUTH-002 | fixed |
| BUG-003 | 🔴 | Beat schedule uses wrong task name for stuck-asset cleanup | MEDIA-005 | open |
| BUG-004 | 🔴 | `sign-batch` has no ownership check | CHAT-002 | open |
| BUG-005 | 🟠 | Message history returns 500 when no conversation exists | CHAT-005 | open |
| BUG-006 | 🟠 | WebSocket auth skips client binding; token in query string | RT-001, AUTH-003 | open |
| BUG-007 | 🟠 | `MediaAsset.url` ignores S3 mock mode and assumes a public bucket | CHAT-002, MEDIA-001 | open |
| BUG-008 | 🟡 | Two S3 client modules with different key formats | CHAT-002, USR-004 | open |
| BUG-009 | 🟡 | `mark_messages_as_read` updates `unread_counts` without a row lock | CHAT-005 | open |
| BUG-010 | 🟡 | `chat_typing` can be sent to any user ID | RT-002 | open |
| BUG-011 | 🟡 | Container boot runs `makemigrations`; `DEBUG`/`ALLOWED_HOSTS` hardcoded | OPS | open |
| BUG-012 | ⚪ | Dead `PENDING` status path and removed `processing_progress` field still referenced | CHAT-002, MEDIA-005 | open |
| BUG-013 | 🟠 | Stale tests reference removed code/endpoints | OPS | open |
| BUG-014 | 🟡 | Contacts list ordering by name is overridden by cursor pagination | USR-002 | open |

---

## BUG-001 — `send-otp` returns the OTP in the response body
- **Severity:** 🔴 critical (account takeover) · **Status:** open · **Spec:** AUTH-002.AC2
- **Where:** `server/users/views.py`, `SendOTPView.post`: `data={"otp": otp, "token": email_token}`
- **Problem:** Anyone who knows a registered email can call `send-otp`, read the OTP from the response, and call `verify-otp` to get JWTs for that user.
- **Expected:** The response contains only the email token. The OTP is delivered by email only.

## BUG-002 — Tasks without explicit queue go to an unconsumed queue
- **Severity:** 🔴 high · **Status:** fixed · **Specs:** RT-003, AUTH-001, AUTH-002
- **Where:** `server/background_worker/celery.py`; `send_templated_email_task`, `mark_delivered_and_notify_senders`.
- **Problem:** Celery's default queue was named `celery`. docker-compose workers consume only `default`, `image_queue,audio_queue,file_queue`, and `video_queue`, so welcome/OTP emails and delivery receipts were never executed.
- **Fix:** Set `app.conf.task_default_queue = 'default'` in `celery.py`, routed `send_templated_email_task`, `mark_delivered_and_notify_senders`, and `cleanup_stuck_assets` explicitly in `task_routes`, and added `queue='default'` to task decorators.

## BUG-003 — Beat schedule uses the wrong task name
- **Severity:** 🔴 high · **Status:** open · **Spec:** MEDIA-005
- **Where:** `server/background_worker/celery.py`, `beat_schedule['cleanup-stuck-assets-hourly']['task'] = 'chats.tasks.cleanup_stuck_assets'`
- **Problem:** The registered name is `background_worker.chats.tasks.cleanup_stuck_assets`, so the scheduled task is unregistered and never runs; stuck assets are never failed.
- **Expected:** The beat entry references the registered task name.

## BUG-004 — `sign-batch` has no ownership check
- **Severity:** 🔴 high · **Status:** open · **Spec:** CHAT-002
- **Where:** `server/chats/views.py`, `SignBatchView.post`
- **Problem:** Any authenticated user can request presigned `upload_part` URLs for any `object_key` + `upload_id` and overwrite another user's in-progress upload.
- **Expected:** The server verifies that a `MediaAsset` with that `object_key` exists, is `queued`, and belongs to a message sent by the requester.

## BUG-005 — Message history returns 500 when no conversation exists
- **Severity:** 🟠 medium · **Status:** open · **Spec:** CHAT-005
- **Where:** `server/chats/views.py`, `ChatMessageListView.get`: `self.pagination_class().get_paginated_response([])`
- **Problem:** `CursorPagination.get_next_link()` reads `self.has_next`, which is only set by `paginate_queryset()`. Opening a chat with a new partner raises `AttributeError`, which becomes a 500.
- **Expected:** 200 with `messages: []`, `next: null`, `previous: null`.

## BUG-006 — WebSocket auth skips client binding; token in query string
- **Severity:** 🟠 medium · **Status:** open · **Specs:** RT-001, AUTH-003
- **Where:** `server/middlewares/websocket_middleware.py`
- **Problem:** `JWTClientBindingASGIMiddleware` decodes the access token but never calls `verify_token_signature`, so a stolen token works from any client over WS. `ASGIRequestAdapter` exists but is unused. The token travels in the query string, where it is logged by proxies and access logs.
- **Expected:** WS auth enforces the same client binding as HTTP. Consider moving the token to a subprotocol header or a first-message auth.

## BUG-007 — `MediaAsset.url` ignores mock mode and assumes a public bucket
- **Severity:** 🟠 medium · **Status:** open · **Specs:** CHAT-002, MEDIA-001
- **Where:** `server/chats/models.py`, `MediaAsset.url` / `thumbnail_url`
- **Problem:** URLs are always `https://<bucket>.s3.amazonaws.com/<key>`. With `USE_S3_MOCK=True` they point to real AWS (broken locally). `ChatUser.avatar_url` handles mock mode, but media doesn't. Public URLs also mean anyone with a link can read private chat media.
- **Expected:** Mock-aware URLs. A decision (ADR) on public vs presigned/CDN-signed media URLs.

## BUG-008 — Two S3 client modules
- **Severity:** 🟡 low · **Status:** open · **Specs:** CHAT-002, USR-004
- **Where:** `server/utils/aws.py` and `server/utils/s3.py`
- **Problem:** Both create boto3 clients and define `new_object_key` with different formats (`chat_uploads/{uid}/{uuid}/{name}` vs `uploads/user_{uid}/{uuid}/{name}`). `chats/views.py` uses `utils.aws`; `chats/services.py` and `users/services.py` use `utils.s3`. The test fixture patches only `utils.aws.s3`.
- **Expected:** One S3 module.

## BUG-009 — Read-modify-write on `unread_counts` without a lock
- **Severity:** 🟡 low · **Status:** open · **Spec:** CHAT-005
- **Where:** `server/chats/services.py`, `ChatService.mark_messages_as_read`
- **Problem:** The conversation is not re-fetched with `select_for_update()`. A concurrent `send_text_message` can lose its unread increment, or the reset can be overwritten.
- **Expected:** Lock the conversation row (or use an atomic JSON update) when changing `unread_counts`.

## BUG-010 — `chat_typing` can be sent to any user
- **Severity:** 🟡 low · **Status:** open · **Spec:** RT-002
- **Where:** `server/channel/consumers.py`, `_handle_chat_typing`
- **Problem:** No check that the receiver has a conversation or contact relationship with the sender. Any user can spam typing events to any user ID.
- **Expected:** Typing events are forwarded only to users with an existing conversation (or contact).

## BUG-011 — Unsafe container boot and settings
- **Severity:** 🟡 low · **Status:** open · **Spec:** OPS
- **Where:** `server/entrypoint.sh`, `server/core/settings.py`
- **Problem:** Boot runs `makemigrations`, which can generate unreviewed migrations in production. `DEBUG = True` and `ALLOWED_HOSTS = ["*"]` are hardcoded even though `DJANGO_DEBUG` is passed in. `EMAIL_HOST_USER` is hardcoded.
- **Expected:** Only `migrate` at boot. Read `DEBUG`, `ALLOWED_HOSTS`, and `EMAIL_HOST_USER` from the environment.

## BUG-012 — Dead status path and removed field references
- **Severity:** ⚪ smell · **Status:** open · **Specs:** CHAT-002, MEDIA-005
- **Where:** `server/background_worker/chats/tasks.py`
- **Problem:** Services never create messages with status `pending` (status is computed from presence), yet `_handle_failure` branches on `pending`. `_finalize_asset` sets `asset.processing_progress`, a field removed in migration 0007.
- **Expected:** Decide whether media messages start as `pending` (spec CHAT-002), and remove dead references.

## BUG-013 — Stale tests
- **Severity:** 🟠 medium · **Status:** open · **Spec:** OPS
- **Where:** `server/tests/background_worker/test_chat_tasks.py`, `server/tests/chats/test_views.py`, `server/tests/channel/test_user_socket_consumer.py`, `server/tests/constants.py`
- **Problem:** Tests import or patch removed symbols (`process_uploaded_asset`, `notify_message_event`), call removed endpoints (`/api/chat/upload/prepare/`), and assert old WS payloads (`"Authentication failed"`, `success` key).
- **Expected:** Rewritten against the specs in the TDD phase.

## BUG-014 — Contacts ordering overridden
- **Severity:** 🟡 low · **Status:** open · **Spec:** USR-002
- **Where:** `server/users/views.py` (`GetContactsView`: `.order_by('contact_user__full_name')`) vs `server/users/pagination.py` (`ContactCursorPagination.ordering = '-created_at'`)
- **Problem:** `CursorPagination` applies its own `ordering`, so contacts come back newest-first, not alphabetically. The explicit `order_by` is dead code.
- **Expected:** Decide the intended order (spec USR-002) and make the code match it.
