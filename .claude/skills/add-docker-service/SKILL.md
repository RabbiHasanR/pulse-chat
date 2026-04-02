---
name: add-docker-service
description: Guided workflow for adding a new containerized service to Pulse Chat — optimized multi-stage Dockerfile, .dockerignore, entrypoint.sh, and docker-compose entry.
---

Add a new Docker service. Wait for approval at each step before writing files.

$ARGUMENTS: service name/description (e.g. "frontend", "nginx", "celery worker")

## Step 1 — Clarify
Ask: runtime (Python/Node/Nginx/other)? needs db/redis wait? init steps on startup? exposes a port? needs `*app-env`? dev live-reload volume?

## Step 2 — Read Existing Patterns
Read `server/Dockerfile`, `server/entrypoint.sh`, `docker-compose.yml`, `database/Dockerfile` before designing anything.

## Step 3 — Dockerfile

**Optimization rules for every Dockerfile:**
- Multi-stage when there are build tools (compiler, devDeps) — keeps them out of the final image
- Copy dependency manifest first, source last — maximizes layer cache hits
- One `RUN` per logical step; clean apt cache in the same step: `&& rm -rf /var/lib/apt/lists/*`
- `--no-install-recommends` for apt, `--no-cache-dir` for pip
- Pin exact image versions — never `latest`

**Patterns by runtime:**

*Python* — multi-stage always:
- `builder`: slim base + gcc/build-essential + `pip install --user --no-cache-dir`
- `runtime`: slim base + runtime-only system deps + `COPY --from=builder /root/.local`

*Node* — multi-stage always:
- `deps`: alpine + `npm ci --only=production`
- `build`: alpine + `npm ci` + `npm run build`
- `runtime`: alpine + `COPY --from=deps node_modules` + `COPY --from=build dist`

*Nginx* — `nginx:alpine` (8 MB); copy built assets from a Node build stage if needed.

*Thin wrapper* — `FROM image:tag` + `EXPOSE port` only when no customisation needed.

Show Dockerfile with size justification. Wait for approval.

## Step 4 — .dockerignore
Always create alongside the Dockerfile. Exclude: `__pycache__/`, `*.pyc`, `node_modules/`, `dist/`, `.env`, `.git/`, `tests/`, `*.md`, `htmlcov/`.
Prevents secrets and cache from entering the build context.

## Step 5 — entrypoint.sh (if needed)
Only when service needs a readiness wait or init commands:
```sh
#!/bin/sh
set -e
until nc -z "$HOST" "$PORT"; do echo "Waiting..."; sleep 1; done
# init steps
exec "$@"
```
`exec "$@"` — passes signals to the app process (graceful shutdown). `set -e` — fails fast on broken init.

## Step 6 — docker-compose entry
Follow existing service structure: `build`, `container_name`, `hostname`, `environment: *app-env` (only if needed), `depends_on` with `condition: service_healthy`, `healthcheck`. Healthcheck probes: HTTP → `curl -f`, TCP → `nc -z`, Redis → `redis-cli ping`, Postgres → `pg_isready`.

## Step 7 — Write Files
1. `Dockerfile` + `.dockerignore` in service directory
2. `entrypoint.sh` if needed → `chmod +x`
3. Add service block to `docker-compose.yml`

## Step 8 — Verify
```bash
docker compose config            # validate syntax
docker compose build service_name
docker image ls | grep service_name   # check final size
docker history service_name      # find bloat if size is unexpected
docker compose up -d service_name && docker compose logs -f service_name
```
Report final image size. Flag if over threshold: Python > 300 MB, Node > 200 MB, Nginx > 50 MB.
