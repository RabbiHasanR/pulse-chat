# Pulse Chat — Agent Guidelines (AGENTS.md)

This file is the **universal entry point** for all AI coding assistants (Google Antigravity, Claude Code, OpenAI Codex, Cursor, GitHub Copilot, etc.) working on Pulse Chat.

---

## 1. Project Overview & Architecture
Pulse Chat is a distributed 1-to-1 real-time chat application and system-design learning codebase:
- **Backend**: Django 6, Django REST Framework, Django Channels (ASGI), Celery.
- **Data & Storage**: PostgreSQL 15, Redis 7 (Broker DB 0, Channel Layer & Ephemeral State DB 1, Cache DB 2), AWS S3 (Moto locally).
- **Core Documentation**:
  - Architecture overview: [docs/architecture/overview.md](docs/architecture/overview.md)
  - Relational schema & indexes: [docs/architecture/data-model.md](docs/architecture/data-model.md)
  - Real-time WebSocket mechanics: [docs/architecture/realtime.md](docs/architecture/realtime.md)
  - Direct S3 upload pipeline: [docs/architecture/media-pipeline.md](docs/architecture/media-pipeline.md)
  - REST API Contract: [docs/contracts/rest-api.md](docs/contracts/rest-api.md)
  - WebSocket Protocol: [docs/contracts/websocket-events.md](docs/contracts/websocket-events.md)
  - Known issues & bug registry: [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)

---

## 2. The Spec-Driven Workflow (Golden Rules)

1. **Specs are the Single Source of Truth**:
   All features, endpoints, WebSocket events, and state mutations are governed by specifications under [docs/specs/](docs/specs/README.md).
2. **Read the Governing Spec Before Changing Behavior**:
   Before modifying existing endpoints or services, find the spec that lists the file in its `code:` frontmatter.
3. **No Code Without an `approved` Spec**:
   - For new features or behavioral changes, first write or update a spec using `spec-write`.
   - Specs start in `status: draft`.
   - **Agents must NEVER self-approve specs.** An agent may transition a spec to `status: approved` **only after the human maintainer explicitly gives approval in the chat**.
4. **Synchronized Commits**:
   When implementing behavior, update the spec's `status` to `implemented`, update `last_verified`, and update contracts ([docs/contracts/](docs/contracts/)) within the same commit.
5. **Validate Specs Automatically**:
   Always run the specification validator before completing a turn:
   ```bash
   python3 .agents/skills/spec-sync/scripts/check_specs.py
   ```

---

## 3. Universal Skills Index (`.agents/skills/`)

Skills are modular runbooks located in `.agents/skills/<name>/SKILL.md`:

| Skill | Purpose |
| --- | --- |
| `spec-write` | Guided workflow to draft or update feature specifications. |
| `spec-implement` | Guided workflow to implement an approved specification. |
| `spec-sync` | Audits code against spec acceptance criteria and runs validator. |
| `adr-write` | Records architectural decisions and technical trade-offs. |
| `add-endpoint` | Guided workflow for creating new REST API endpoints. |
| `add-ws-event` | Guided workflow for adding real-time WebSocket events. |
| `add-celery-task` | Guided workflow for isolated background worker tasks. |
| `add-test` | Guided test creation using project fixtures. |
| `learn` | Explores system-design concepts across multiple variants. |
| `add-docker-service` | Adds optimized container services to docker-compose. |
| `review-django-patterns` | Audits Django code for project pattern violations. |
| `review-celery-task` | Audits Celery task decorator configs and queue routing. |
| `review-migration` | Reviews Django migration files for table lock and safety risks. |

---

## 4. Operational Commands (Docker Compose V2)

Run these from the repository root:
```bash
# Start all containers in background
docker compose up -d

# Tail server or worker logs
docker compose logs -f server
docker compose logs -f celery_video

# Stop containers
docker compose down
```

---

## 5. Commit Message Conventions

Follow Conventional Commits: `type(scope): description`.
- **Types**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- **Scopes**: `auth`, `users`, `chats`, `realtime`, `media`, `specs`, `adr`, `agents`, `infra`.
- Examples:
  - `docs(specs): add CHAT-001 specification`
  - `fix(auth): prevent OTP leakage in send-otp response`
