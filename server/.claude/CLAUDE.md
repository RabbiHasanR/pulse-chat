# Server — Django Backend

Django REST + Channels backend for Pulse Chat. Loaded alongside global and root `.claude/CLAUDE.md`.

## Apps

| App | Responsibility |
| --- | --- |
| `users` | Auth (email + OTP), JWT, profiles, contacts, avatar (S3) |
| `chats` | Conversations, messages, media assets, multipart upload |
| `channel` | WebSocket consumer, real-time event dispatch |
| `background_worker` | Celery tasks — media processing, email, delivery receipts |
| `middlewares` | JWT client-binding auth (HTTP + WebSocket) |
| `utils` | Shared helpers — see below |

## Key Utilities — Use These, Don't Reinvent

| Module | Provides |
| --- | --- |
| `utils/response.py` | `success_response()`, `error_response()` — use on all view returns |
| `utils/redis_client.py` | `RedisKeys` (key names), `ChatRedisService`, `redis_client` (async), `sync_redis_client` |
| `utils/s3.py` | S3 client, presigned URL helpers, Moto mock support |
| `utils/pagination.py` | Cursor pagination — use on all list endpoints |
| `utils/jwt_util.py` | Token issuance + verification |
| `tests/constants.py` | URL constants for all test assertions |

## Patterns

### New REST endpoint

- Logic in `<app>/services.py`, never in the view
- View: `APIView` subclass, `permission_classes = [IsAuthenticated]`
- URL: kebab-case, no trailing slash
- Use `/add-endpoint` skill for the full guided workflow

### New Celery task

- Required config: `bind=True`, `acks_late=True`, `max_retries`, `soft_time_limit`, `time_limit` (> soft)
- Pass IDs, not objects (JSON serialization)
- Register in `task_routes` in `background_worker/celery.py`
- Queues: `default`, `image_queue`, `audio_queue`, `file_queue`, `video_queue` (FFmpeg only)
- Use `/add-celery-task` skill for the full guided template

## Test Fixtures (`tests/conftest.py`)

| Fixture | Use for |
| --- | --- |
| `auth_client` | Authenticated DRF `APIClient` with JWT + client headers |
| `fake_redis` | In-memory Redis mock (set/get/sadd/srem/expire/keys/mget) |
| `patch_redis` | Monkeypatches `channel.consumers.redis_client` → `fake_redis` |
| `s3_client` | Moto S3 with test bucket pre-created |
| `patch_global_s3_client` | Patches `utils.aws.s3` for all S3 usage across modules |
| `helpers` | Async WS helpers: `recv_json`, `recv_until`, `recv_type` |
| `chat_message` | Text `ChatMessage` (status=PENDING) between `user` and `another_user` |
| `media_asset` | `MediaAsset` linked to `chat_message` (processing_status=queued) |

Async tests need `@pytest.mark.asyncio`.

## Anti-Patterns — Never Do These

- **Offset pagination** — cursor only (`utils/pagination.py`)
- **Raw Redis key strings** — use `RedisKeys` class, never `f"user:{id}:..."` directly
- **Direct `boto3.client('s3')`** — use `utils/s3.py` helpers only
- **`channel_layer.group_send` in views** — use `ChatRedisService` methods
- **Business logic in views** — belongs in `<app>/services.py`
- **ViewSets** — project uses `APIView` consistently
- **`.all()` without filtering** on large tables (`ChatMessage`, `Conversation`)
- **Missing `select_related`** on FK fields in list queries

## Testing Conventions

File locations: `tests/<app>/test_<views|tasks|consumers>.py`

Fixture combos by scenario:

| Scenario | Fixtures |
| --- | --- |
| REST endpoint | `auth_client` |
| WebSocket consumer | `patch_redis` + `fake_redis` + `helpers` + `@pytest.mark.asyncio` |
| Celery task | `patch_global_s3_client` + `fake_redis` |
| Media task | `patch_global_s3_client` + `fake_redis` + `media_asset` |
| Middleware | `mock_request` + `issue_bound_token` |

Naming: `test_<action>_<condition>` — e.g. `test_send_message_unauthorized`, `test_process_image_s3_error`

Use URL constants from `tests/constants.py`, never hardcode paths.
Minimum cases per endpoint: happy path + missing/invalid fields + unauthorized.
