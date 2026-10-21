# Pulse Chat — Backend Architecture & Rules (`server/AGENTS.md`)

This file guides all AI agents working inside the `server/` Django application directory.

---

## 1. App Responsibilities

| App | Responsibility |
| --- | --- |
| `users` | Auth (email + OTP), JWT token issuance/refresh, profiles, contacts, avatars (S3) |
| `chats` | Conversations, messages, media assets, presigned upload orchestration |
| `channel` | WebSocket consumer (`UserSocketConsumer`), presence, typing events |
| `background_worker` | Celery tasks — video HLS transcoding, image/audio/file processing, email delivery |
| `middlewares` | HTTP JWT client-binding middleware, ASGI WS authentication middleware |
| `utils` | Shared infrastructure: responses, cursor pagination, redis client, s3 client, media processors |

---

## 2. Mandatory Coding Patterns

### REST Endpoints
- **Service Layer**: All business logic belongs in `<app>/services.py`. Views must be thin controllers.
- **View Base Class**: Subclass `rest_framework.views.APIView`. Never use `ViewSet` or `ModelViewSet`.
- **Response Format**: Always return `success_response()` or `error_response()` from `utils/response.py`.
- **URL Routing**: Use trailing slashes consistently (e.g. `path('send/', ...)`).
- **Pagination**: Use cursor pagination only (`utils/pagination.py`). Never use `OFFSET` / `LIMIT` or page numbers.

### Celery Tasks
- **Decorator Config**: Required parameters: `bind=True`, `acks_late=True`, `max_retries`, `soft_time_limit`, `time_limit`.
- **Serialization**: Pass primary key IDs (integers), never model instances.
- **Queue Routing**: Explicitly register task in `background_worker/celery.py` `task_routes`.
- **Queues**:
  - `video_queue`: Dedicated to FFmpeg video transcoding jobs.
  - `image_queue`, `audio_queue`, `file_queue`: Media processing.
  - `default`: Low-latency notifications, emails, and maintenance sweepers.

### S3 Storage & Uploads
- Use the unified helpers in `utils/s3.py`.
- Never buffer raw media binary uploads in Django server memory; use direct-to-S3 presigned URLs.

### Redis & Real-Time Presence
- Always access Redis keys through `RedisKeys` static methods in `utils/redis_client.py`. Never construct raw key strings (e.g. `f"user:{id}:..."`) in view or consumer code.
- Outbound socket messages from views/services route to the single user group `user_{user_id}`.

---

## 3. Anti-Patterns (Strictly Forbidden)

- ❌ `OFFSET` / `LIMIT` pagination (prohibited by ADR-0002).
- ❌ Raw Redis key string formatting outside `RedisKeys` (prohibited by ADR-0008).
- ❌ Direct `boto3.client('s3')` in view code (use `utils/s3.py`).
- ❌ Business logic in view functions or classes (must reside in `<app>/services.py`).
- ❌ ViewSets / ModelViewSets.
- ❌ Unfiltered `.all()` queries on `ChatMessage` or `Conversation`.
- ❌ Missing `select_related` / `prefetch_related` on foreign key queries in list endpoints.

---

## 4. Test Conventions

- Test directory: `server/tests/<app>/test_<views|tasks|consumers>.py`.
- Reference spec acceptance criteria in test docstrings: `"""CHAT-001.AC1"""`.
- Core fixtures in `tests/conftest.py`:
  - `auth_client`: Authenticated DRF `APIClient` with bound JWT.
  - `fake_redis`: In-memory Redis simulation for async Channels tests.
  - `patch_redis`: Patches `channel.consumers.redis_client`.
  - `s3_client`: Moto in-memory S3 client with test bucket pre-created.
