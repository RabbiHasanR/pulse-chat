# ADR-0001: Adopt Spec-Driven Development & Agent-Agnostic Governance

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** All specs

## Context
Pulse Chat is developed as both a production-ready application and an educational reference implementation for distributed system design. Multiple AI coding agents (Antigravity, Claude Code, OpenAI Codex, Cursor, GitHub Copilot) collaborate alongside human contributors. Without machine-readable specifications, acceptance criteria, and formal contracts, agent interactions frequently introduce subtle regressions, hallucinate non-existent API rules (e.g. trailing slash inconsistencies), or leave features only partially verified.

## Decision
We adopt **Spec-Driven Development (SDD)** with tool-agnostic standards:
1. All behavior, interfaces, and state changes are codified in markdown specifications under `docs/specs/<area>/` with unique IDs (`<AREA>-<NNN>`) and testable acceptance criteria (`<SPEC>.AC<n>`).
2. Central repository rules are defined in standard `AGENTS.md` (root and `server/AGENTS.md`) recognized universally across AI coding assistants.
3. Every feature change must be governed by an `approved` spec before writing production code.
4. An automated standard-library script (`.agents/skills/spec-sync/scripts/check_specs.py`) validates spec integrity on every commit.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Tool-specific configs (`.claude/` only) | Native Claude Code support | Ignored by Antigravity, Cursor, Codex, and other agents. Causes fragmentation. |
| Code comments / informal docs | Low barrier to entry | Inevitably drifts from implementation; lacks numbered ACs for test traceability. |

## Consequences
**Positive**
- Any AI agent or human contributor has a deterministic source of truth.
- Acceptance criteria provide direct requirements for test suite implementation in TDD.
- Automated validation prevents broken links and unmaintained spec drift.

**Rules this creates**
- No behavior change is merged without an updated, approved spec.
- Agents must never approve their own proposed specs.
