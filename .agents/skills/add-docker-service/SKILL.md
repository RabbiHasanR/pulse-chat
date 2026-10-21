---
name: add-docker-service
description: >-
  Guided workflow for adding a new containerized service to Pulse Chat —
  optimized multi-stage Dockerfile, .dockerignore, entrypoint.sh, and docker-compose entry.
---

# add-docker-service — Add Containerized Service

Add a new Docker service. Wait for approval at each step before writing files.

**Input:** Service name and description (e.g., "frontend", "nginx", "celery worker").

## Step 1 — Clarify Requirements
Ask the user:
- Runtime (Python, Node, Nginx, or other)?
- Does it need DB/Redis readiness wait?
- Any initialization steps on startup?
- Does it expose a port?
- Does it need the shared `*app-env` YAML anchor?
- Does it need a live-reload bind volume for development?

## Step 2 — Read Existing Patterns
Read existing configs before designing:
- `server/Dockerfile`
- `server/entrypoint.sh`
- `docker-compose.yml`
- `database/Dockerfile`

## Step 3 — Dockerfile Design

**Optimization rules for every Dockerfile:**
- **Multi-stage builds** when build tools (gcc, build-essential, devDependencies) are needed — keep them out of final image.
- **Copy dependency manifest first**, source code last — maximizes layer cache hits.
- **Single `RUN`** per logical step with cleanup in the same layer: `&& rm -rf /var/lib/apt/lists/*`.
- `--no-install-recommends` for `apt-get`, `--no-cache-dir` for `pip`.
- Pin exact image tags — never use `:latest`.

**Patterns by runtime:**
- **Python** (multi-stage always):
  - `builder`: slim base + build-essential + `pip install --user --no-cache-dir -r requirements.txt`
  - `runtime`: slim base + runtime-only system libs + `COPY --from=builder /root/.local /root/.local`
- **Node** (multi-stage always):
  - `deps`: alpine + `npm ci --only=production`
  - `build`: alpine + `npm ci` + `npm run build`
  - `runtime`: alpine + `COPY --from=deps node_modules` + `COPY --from=build dist`
- **Nginx**:
  - `nginx:alpine` (~8 MB); copy static assets from build stage if applicable.
- **Thin wrapper**:
  - `FROM image:tag` + `EXPOSE port` only when no custom build is needed.

Show Dockerfile with size justification to user. Wait for approval.

## Step 4 — .dockerignore
Always create `.dockerignore` alongside the Dockerfile. Exclude:
- `__pycache__/`, `*.pyc`, `*.pyo`
- `node_modules/`, `dist/`, `build/`
- `.env`, `.env.*`
- `.git/`, `.gitignore`
- `tests/`, `htmlcov/`, `.coverage`
- `*.md`, `docs/`

## Step 5 — entrypoint.sh (if needed)
Only when service requires a readiness probe or initialization steps:
```sh
#!/bin/sh
set -e

until nc -z "$HOST" "$PORT"; do
  echo "Waiting for $HOST:$PORT..."
  sleep 1
done

# Run init steps (migrations, static collection, etc.)
exec "$@"
```
- `exec "$@"` ensures proper signal propagation (SIGTERM/SIGINT) for graceful shutdown.
- `set -e` fails fast on broken initialization.

## Step 6 — docker-compose.yml Entry
Follow existing service conventions in `docker-compose.yml`:
- `build: ...`
- `container_name: ...`
- `hostname: ...`
- `environment: *app-env` (only if using shared Django environment)
- `depends_on: { service: { condition: service_healthy } }`
- `healthcheck:` with appropriate probe:
  - HTTP: `curl -f http://localhost:PORT/path || exit 1`
  - TCP: `nc -z localhost PORT || exit 1`
  - Redis: `redis-cli ping | grep PONG`
  - Postgres: `pg_isready -U user -d db`

## Step 7 — Write Files
1. Create `Dockerfile` and `.dockerignore` in the service directory.
2. Create `entrypoint.sh` if needed and mark executable (`chmod +x`).
3. Add the service definition to `docker-compose.yml`.

## Step 8 — Verify
```bash
docker compose config                     # Validate compose syntax
docker compose build <service_name>       # Build image
docker image ls | grep <service_name>     # Inspect image size
docker compose up -d <service_name>       # Start service
docker compose logs -f <service_name>     # Verify startup logs
```
Image size thresholds:
- Python runtime: < 300 MB
- Node runtime: < 200 MB
- Nginx runtime: < 50 MB
