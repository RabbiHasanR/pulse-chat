---
name: add-docker-service
description: Guided workflow for adding a new containerized service to Pulse Chat — optimized multi-stage Dockerfile, .dockerignore, entrypoint.sh, and docker-compose entry following project patterns.
---

Add a new Docker service to the project with an optimized, minimal image. Follow each step in order. Wait for approval before writing any file.

$ARGUMENTS: name or description of the new service (e.g. "frontend", "nginx", "redis exporter")

## Step 1 — Clarify

Ask if not already clear:
- What does this service run? (Python, Node, Nginx, a DB tool, etc.)
- Is this a dev-only service or will it run in production?
- Does it need to wait for other services before starting? (db, redis)
- Does it need init steps on startup? (migrations, seed data, config generation)
- Does it need app environment variables (`*app-env`)? Or its own env vars?
- Does it expose a port externally?
- Does it need live code reload (mount `./dir:/app` volume)?

## Step 2 — Read Existing Patterns

Before writing anything, read:
- `server/Dockerfile` — multi-stage Python build used in this project
- `server/entrypoint.sh` — DB wait + migration init pattern
- `docker-compose.yml` — service structure, `*app-env` anchor, health checks, `depends_on`
- `database/Dockerfile` — minimal third-party wrapper pattern

New services must be consistent with existing ones.

## Step 3 — Design the Dockerfile

### Core optimization rules (apply to every Dockerfile)

**1. Layer caching — order matters**
Always copy dependency manifests first, install deps, then copy source code last.
```dockerfile
COPY requirements.txt .   # ← copied first
RUN pip install ...        # ← cached until requirements.txt changes
COPY . .                   # ← only invalidates pip cache when source changes
```
Why: Docker caches each layer. Changing a single source file won't re-run pip install if requirements.txt didn't change.

**2. Combine RUN commands into one layer**
```dockerfile
# Bad — 3 layers, intermediate apt state cached
RUN apt-get update
RUN apt-get install -y curl
RUN rm -rf /var/lib/apt/lists/*

# Good — 1 layer, cache cleaned in the same step
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*
```
Why: Every `RUN` creates a layer. Cleaning caches in a separate layer doesn't actually shrink the image — the cache bytes are already committed.

**3. `--no-install-recommends` for apt**
Skips suggested packages that are almost never needed in containers. Can cut 20–50 MB.

**4. Never use `latest` tag**
Pin exact versions: `python:3.13.3-slim`, not `python:latest`. Reproducible builds.

**5. Use `.dockerignore`**
Always create one. Prevents sending unnecessary files to the build context (`.git`, `__pycache__`, `*.pyc`, local envs, test files). Speeds up build and avoids leaking secrets.

---

### Choose the right pattern for this service

#### Python service (app server, Celery worker, background process)

Use **multi-stage build** — builder installs deps with build tools, runtime has only what's needed to run:

```dockerfile
# ── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.13.3-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build-only tools (gcc, etc.) — NOT carried into runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first (layer cache)
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.13.3-slim AS runtime

WORKDIR /app

ENV PATH="/root/.local/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Only runtime system deps (no gcc, no build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only installed packages from builder — no build tools come along
COPY --from=builder /root/.local /root/.local

# Source code last — doesn't invalidate dep cache
COPY . .

EXPOSE <port>
CMD [...]
```

**Why multi-stage?** gcc, build-essential, and pip itself are only needed to compile and install packages. They add ~200–300 MB and are a security risk in production. The runtime stage copies the compiled packages but leaves all build tools behind.

**Why `--no-cache-dir` for pip?** The pip download cache is useless inside a container — the layer cache handles reuse. `--no-cache-dir` prevents pip from writing ~50–100 MB of cached wheels to the image.

**Why `--user` pip install?** Installs to `/root/.local` instead of system Python. Cleaner separation between system packages and app packages.

---

#### Node service (frontend, SSR, API)

Use **multi-stage build** — build stage compiles assets, runtime stage serves them:

```dockerfile
# ── Stage 1: deps ────────────────────────────────────────────────
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --only=production   # exact versions from lockfile, no devDeps

# ── Stage 2: build ───────────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci                     # include devDeps for build tools
COPY . .
RUN npm run build              # compile TypeScript, bundle assets, etc.

# ── Stage 3: runtime ─────────────────────────────────────────────
FROM node:22-alpine AS runtime
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules   # prod deps only
COPY --from=build /app/dist ./dist                  # compiled output only
EXPOSE <port>
CMD ["node", "dist/index.js"]
```

**Why alpine?** node:22-alpine is ~50 MB vs ~350 MB for node:22. Contains no unnecessary OS packages.

**Why `npm ci`?** Installs exact versions from `package-lock.json`. Faster and deterministic vs `npm install`.

**Why three stages?** `node_modules` with devDeps can be 500+ MB. The runtime image gets prod deps + compiled output only — typically 80–150 MB total.

---

#### Static file server (Nginx)

Multi-stage if building assets; single-stage if just serving pre-built files:

```dockerfile
# If building first (e.g. React/Vue app):
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**Why nginx:alpine?** Only 8 MB. Contains only nginx and its runtime deps.

---

#### Thin wrapper (third-party image, no customisation)

```dockerfile
FROM postgres:15-alpine
EXPOSE 5432
```

Only use this when the official image is sufficient and you just need to label it for the project. If you need init scripts, mount them as a volume instead of copying into the image.

---

Show the Dockerfile design to the user with size/optimization notes. Wait for approval.

## Step 4 — Create .dockerignore

Always create a `.dockerignore` in the service directory alongside the Dockerfile.

For Python services:
```
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
.eggs/
dist/
build/
.pytest_cache/
.coverage
htmlcov/
env/
venv/
.env
.git/
.gitignore
*.md
tests/
```

For Node services:
```
node_modules/
dist/
build/
.env
.git/
.gitignore
*.md
coverage/
.nyc_output/
```

**Why `.dockerignore` matters:**
- Excludes files from the build context sent to the Docker daemon
- `COPY . .` won't accidentally include `.env` secrets, `.git` history, or `node_modules`
- Smaller build context = faster builds

Show to user. Wait for approval.

## Step 5 — Design entrypoint.sh (if needed)

Only create if the service needs a readiness wait or init commands.

```sh
#!/bin/sh
set -e

# Wait for a TCP dependency to be ready
until nc -z "$HOST" "$PORT"; do
  echo "Waiting for $HOST:$PORT..."
  sleep 1
done

# Init steps (migrations, config, etc.)

exec "$@"
```

**Why `exec "$@"`**: Replaces the shell with the CMD process. Signals (SIGTERM, SIGINT) go directly to the app — critical for graceful shutdown. Without `exec`, the shell catches the signal and the app never shuts down cleanly.

**Why `set -e`**: Exits on any command failure. Prevents the service starting in a broken state (e.g. migration failed but container appears healthy).

**Why `nc -z` and not `sleep`**: Polls the actual TCP port, not a fixed delay. Starts as soon as the dependency is ready instead of waiting a fixed number of seconds.

Show to user. Wait for approval.

## Step 6 — Design docker-compose entry

Follow the existing project structure:

```yaml
service_name:
  build:
    context: ./service_dir
    dockerfile: Dockerfile
  container_name: service_name
  hostname: service_name
  environment: *app-env             # only if service needs app env vars
  ports:
    - "host_port:container_port"    # only if externally accessible
  entrypoint: /app/entrypoint.sh    # only if entrypoint.sh was created
  command: <start command>
  volumes:
    - ./service_dir:/app            # only for dev live-reload; remove in prod
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

Pick the right healthcheck probe:
- HTTP service: `["CMD", "curl", "-f", "http://localhost:PORT/health"]`
- TCP service: `["CMD", "nc", "-z", "localhost", "PORT"]`
- Redis: `["CMD", "redis-cli", "ping"]`
- Postgres: `["CMD-SHELL", "pg_isready -U $USER -d $DB"]`

**`depends_on` with `condition: service_healthy`** — guarantees the dependency passed its healthcheck, not just that its container started. Without this, the app starts before the DB is accepting connections.

**`*app-env` anchor** — only add if the service actually reads those variables. Adding it unnecessarily exposes secrets to services that don't need them.

Show to user. Wait for approval.

## Step 7 — Write the Files

After all are approved:
1. Create `Dockerfile` in the service directory
2. Create `.dockerignore` in the service directory
3. Create `entrypoint.sh` if needed — run: `chmod +x <service_dir>/entrypoint.sh`
4. Add service block to `docker-compose.yml`

## Step 8 — Measure and Verify

After writing:

```bash
# Validate docker-compose.yml syntax
docker compose config

# Build the new service
docker compose build service_name

# Check image size — compare before/after or against similar services
docker image ls | grep service_name

# Inspect layer sizes to find bloat
docker history service_name

# Start and check logs
docker compose up -d service_name
docker compose logs -f service_name
```

Report the final image size to the user. If it's larger than expected (Python service > 300 MB, Node > 200 MB, Nginx > 50 MB), identify which layer is causing it from `docker history` output and suggest a fix.
