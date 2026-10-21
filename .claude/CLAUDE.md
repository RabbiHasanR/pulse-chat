# Pulse Chat — Claude Code Configuration

@../AGENTS.md

All universal agent instructions, architecture links, spec-driven development rules, and skills are maintained in [AGENTS.md](../AGENTS.md) as the single source of truth.

## Claude Code Quick Reference

- **Universal Skills**: Available via `/skills` (symlinked from `.agents/skills/`).
  - `/spec-write`, `/spec-implement`, `/spec-sync`, `/adr-write`
  - `/add-endpoint`, `/add-ws-event`, `/add-celery-task`, `/add-test`, `/add-docker-service`
  - `/review-django-patterns`, `/review-celery-task`, `/review-migration`, `/learn`
- **Slash Commands**:
  - `/docker-up` — start containers
  - `/docker-down` — stop containers
  - `/docker-logs` — tail container logs
