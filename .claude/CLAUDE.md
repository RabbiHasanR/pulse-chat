# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Pulse Chat** — a Django-based real-time chat application with WebSocket support, Celery async task processing, video/image/audio transcoding, and S3 media storage.

## Running the Project

```bash
# Start all services (PostgreSQL, Redis, S3 mock, Django server, Celery workers)
docker-compose up

# Start services in background
docker-compose up -d

# View logs for a specific service
docker-compose logs -f server
docker-compose logs -f celery_video
```

## Development Setup (without Docker)

```bash
# Activate virtualenv
source server/env/bin/activate

# Install dependencies
pip install -r server/requirements.txt

# Run migrations
python server/manage.py migrate

# Start Django dev server
python server/manage.py runserver 0.0.0.0:8000

# Start Celery worker (separate terminal)
celery -A core worker -l info

# Start Celery beat scheduler (separate terminal)
celery -A core beat -l info
```

All `manage.py` commands must be run from inside `server/`.

## Testing

```bash
# Run all tests (from server/ directory)
pytest

# Run a specific test file
pytest tests/chats/test_views.py

# Run tests for a specific module
pytest tests/users/
pytest tests/background_worker/
pytest tests/channel/

# Run with coverage
pytest --cov=.
```

Pytest config is in `server/pytest.ini`. `DJANGO_SETTINGS_MODULE` is set to `core.settings`.

## Architecture

### Service Layout (docker-compose)

| Service | Purpose |
|---|---|
| `db` | PostgreSQL 15 |
| `redis` | Broker, cache, and Channels layer |
| `s3mock` | Moto S3 mock for local development |
| `server` | Django app (Gunicorn + Uvicorn workers) |
| `celery_default` | Default/notification tasks |
| `celery_media` | Image, audio, and file processing (concurrency 4) |
| `celery_video` | Video transcoding only (concurrency 2, CPU-isolated) |
| `celery_beat` | Periodic task scheduler |

### Django Apps

- **`users/`** — Custom `ChatUser` model, registration/login (OTP-based), contacts, JWT with client binding (IP + user agent)
- **`chats/`** — `Conversation`, `ChatMessage`, `MediaAsset` models; REST endpoints for sending/fetching messages and media uploads
- **`channel/`** — Django Channels WebSocket consumers for real-time delivery receipts and typing indicators
- **`background_worker/`** — Celery tasks split into `chats/tasks.py` (media processing) and `users/tasks.py` (email)
- **`middlewares/`** — JWT client binding, WebSocket authentication, custom exception handler
- **`utils/`** — JWT helpers, Redis client, S3 helpers, pagination, response formatting, media processors

### Key Architectural Patterns

**JWT Client Binding**: Access tokens are tied to a device fingerprint (IP + user agent). See `middlewares/auth_middleware.py`.

**Celery Queue Routing**: Tasks route to specialized queues via Kombu. Video tasks go to `video_queue` (isolated workers), media tasks to `image_queue`/`audio_queue`/`file_queue`, and notifications to `default`.

**Media Upload Flow**: Client signs S3 parts → uploads directly to S3 → calls `/api/chat/complete-upload/` → Celery task picks up transcoding → WebSocket notifies clients of processing status.

**Video Transcoding**: FFmpeg-based HLS output at multiple qualities (240p–1080p). Videos are playable at 240p immediately while higher qualities transcode in the background. See `utils/media_processors/video.py` and `docs/VIDEO_PROCESSING_WORKFLOW.md`.

**Cursor-Based Pagination**: All list endpoints use cursor pagination (not offset). See `utils/pagination.py` and `docs/pagination.md`.

**Caching**: Redis cache-aside pattern for conversations and messages. See `docs/caching.md` for all caching strategies used.

**WebSocket Auth**: JWT validated via custom ASGI middleware in `middlewares/websocket_middleware.py` before reaching Channel consumers.

### API Structure

- `/api/auth/` — Registration, login, token refresh/logout, avatar, contacts
- `/api/chat/` — Conversations, messages, media send/upload/sign
- `/swagger/` and `/redoc/` — API documentation
- WebSocket at `ws://host/ws/chat/`

### Settings of Note (`server/core/settings.py`)

- JWT: HS512 algorithm, 15-minute access tokens, 7-day refresh tokens with rotation
- `USE_S3_MOCK=True` in `.env` points to Moto mock instead of real AWS
- Channel layer and Celery both use Redis

## Commit Message Format

See `server/git-commit-format.txt` for project conventions. Recent commits follow `type(scope): message` format (e.g., `refactor(chats): ...`, `docs(caching): ...`).
