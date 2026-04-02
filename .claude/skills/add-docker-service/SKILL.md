---
name: add-docker-service
description: Guided workflow for adding a new containerized service to Pulse Chat — Dockerfile, entrypoint.sh, and docker-compose entry following project patterns.
---

Add a new Docker service to the project. Follow each step in order and wait for approval before writing files.

$ARGUMENTS: name or description of the new service (e.g. "frontend", "nginx reverse proxy", "redis exporter")

## Step 1 — Clarify

Ask if not already clear:
- What does this service run? (Python/Django, Node, Nginx, a DB tool, etc.)
- Does it need to wait for other services before starting? (e.g. wait for db, redis)
- Does it need init steps on startup? (e.g. run migrations, seed data, generate config)
- Does it need app environment variables (`*app-env`)? Or its own env vars?
- Does it expose a port? Which?
- Does it share the `./server:/app` volume or have its own?

## Step 2 — Read Existing Patterns

Before writing anything, read:
- `server/Dockerfile` — multi-stage Python build pattern used in this project
- `server/entrypoint.sh` — DB readiness wait + migration init pattern
- `docker-compose.yml` — service structure, `*app-env` anchor, health checks, `depends_on`
- `database/Dockerfile` — minimal third-party service pattern

Use these as the reference. New services must be consistent with existing ones.

## Step 3 — Design the Dockerfile

Choose the right pattern based on the service type:

**Python service** (app server, Celery worker, etc.):
- Use multi-stage build: `builder` stage installs deps, `runtime` stage runs the app
- Base image: `python:3.13.3-slim` (match existing)
- Install only system packages the service actually needs
- Copy deps from builder: `COPY --from=builder /root/.local /root/.local`
- Set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`
- Explain why multi-stage: smaller final image, build tools not in production

**Non-Python service** (Node, Nginx, etc.):
- Use official slim/alpine base image
- Single-stage is fine if build complexity is low
- Explain the base image choice

**Thin wrapper** (like `database/Dockerfile`):
- Just `FROM image:tag` + `EXPOSE port` if no customisation needed

Show the Dockerfile to the user. Wait for approval before continuing.

## Step 4 — Design the entrypoint.sh (if needed)

Only create an entrypoint.sh if the service needs:
- Readiness wait (e.g. wait for Postgres or Redis before starting)
- Init commands (migrations, config generation, seeding)

Follow the existing pattern from `server/entrypoint.sh`:
```sh
#!/bin/sh
set -e

# Wait for dependency
until nc -z $HOST $PORT; do
  echo "Waiting for $HOST:$PORT..."
  sleep 1
done

# Init steps here

exec "$@"
```

**Why `exec "$@"`**: replaces the shell process with the CMD, so signals (SIGTERM) reach the actual process — important for graceful shutdown.

**Why `set -e`**: exits immediately if any command fails, preventing the service from starting in a broken state.

Show the entrypoint.sh to the user. Wait for approval before continuing.

## Step 5 — Design the docker-compose entry

Follow this structure, adapting to what the service needs:

```yaml
service_name:
  build:
    context: ./service_dir
    dockerfile: Dockerfile
  container_name: service_name
  hostname: service_name
  environment: *app-env          # only if service needs app env vars
  ports:
    - "host_port:container_port" # only if externally accessible
  entrypoint: /app/entrypoint.sh # only if entrypoint.sh was created
  command: <start command>
  volumes:
    - ./service_dir:/app         # only if live-reload or shared code needed
  depends_on:
    redis:
      condition: service_healthy
    db:
      condition: service_healthy
  healthcheck:
    test: ["CMD", ...]
    interval: 5s
    timeout: 5s
    retries: 5
```

**Key decisions to explain:**
- `*app-env` anchor: reuses the shared env block at the top of docker-compose.yml — only use if the service actually needs those variables
- `depends_on` with `condition: service_healthy`: guarantees dependency is ready, not just started
- healthcheck: pick the right probe for the service type:
  - HTTP service: `curl -f http://localhost:PORT/health`
  - TCP service: `nc -z localhost PORT`
  - Redis: `redis-cli ping`
  - Postgres: `pg_isready -U $USER -d $DB`

Show the docker-compose addition to the user. Wait for approval before writing.

## Step 6 — Write the Files

After all three are approved:
1. Create the `Dockerfile` in the service directory
2. Create `entrypoint.sh` in the service directory (if needed) — make it executable: `chmod +x entrypoint.sh`
3. Add the service block to `docker-compose.yml`

## Step 7 — Verify

After writing:
- Run `docker-compose config` to validate the compose file syntax
- Show the user how to build and test the new service:
  ```bash
  docker-compose build service_name
  docker-compose up -d service_name
  docker-compose logs -f service_name
  ```
