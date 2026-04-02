# Pulse Chat

A Django-based real-time chat application built as both a production-grade system and a learning resource for system design concepts. Anyone can fork, learn, and contribute.

## Project Structure

| Directory | Purpose |
| --- | --- |
| `server/` | Django backend — REST API, WebSocket, Celery workers |
| `database/` | PostgreSQL setup — custom Docker init, schema ownership |
| `frontend/` | (planned) Frontend client |

## Running the Project

```bash
# Start all services
docker-compose up -d

# View logs for a specific service
docker-compose logs -f server
docker-compose logs -f celery_video
```

## Services (docker-compose)

| Service | Purpose |
| --- | --- |
| `db` | PostgreSQL 15 |
| `redis` | Celery broker (DB0), Channels layer (DB1), cache (DB2) |
| `s3mock` | Moto S3 mock for local dev |
| `server` | Django app — Gunicorn + Uvicorn workers |
| `celery_default` | Notifications and lightweight tasks |
| `celery_media` | Image, audio, file processing |
| `celery_video` | HLS video transcoding (CPU-isolated) |
| `celery_beat` | Periodic task scheduler |

## Environment

All services read config from `.env` at the project root. Never commit this file — it contains secrets. Add new variables to `.env` and document them in `docs/` for contributors.
