# Server — Django Backend (Claude Code Configuration)

@../AGENTS.md
@../../AGENTS.md

All backend patterns, app responsibilities, anti-patterns, and test conventions are maintained in [server/AGENTS.md](../AGENTS.md).

## Subagents (`server/.claude/agents/`)
Specialized Claude Code review agents wrapping universal skills:
- `django-pattern-checker`: Wraps `.agents/skills/review-django-patterns/SKILL.md`
- `celery-task-auditor`: Wraps `.agents/skills/review-celery-task/SKILL.md`
- `migration-reviewer`: Wraps `.agents/skills/review-migration/SKILL.md`

## Slash Commands (`server/.claude/commands/`)
- `/test` — run pytest test suite
- `/coverage` — run pytest with coverage report
- `/migrate` — run Django database migrations
