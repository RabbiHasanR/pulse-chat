---
name: spec-sync
description: >-
  Audits that Pulse Chat specs, contracts, and code agree. Use after changing code
  or specs, before committing, when the user asks to "check specs", "sync docs",
  or "verify the spec", or when a spec's last_verified date is stale. Runs the
  validator, compares spec ACs against the governed code, and records drift as
  known issues.
---

# spec-sync — keep specs and code in agreement

**Input:** optional spec ID(s) (e.g. `CHAT-002`) or code path(s). If none, audit everything touched in the current change (`git diff --name-only`).

## Step 1 — Structural validation
Run from the repo root:
```bash
python3 .agents/skills/spec-sync/scripts/check_specs.py
```
It checks frontmatter, unique IDs, AC presence, `code:` paths, spec index ↔ files, BUG references, ADR numbering, skill frontmatter, and relative links. Fix every `ERROR` before continuing. Report `WARN` lines to the user.

## Step 2 — Find the governing specs
- From a code path: `grep -rl "<path>" docs/specs/` (matches the `code:` frontmatter).
- From a spec ID: read the spec and every file in its `code:` list.
- If changed code is governed by **no** spec, tell the user and propose `spec-write` (status `draft`).

## Step 3 — Semantic diff (per spec)
For each AC, read the code path that implements it and classify:

| Result | Action |
| --- | --- |
| Code matches AC | nothing |
| Code differs, and the **spec is right** | add or confirm a `BUG-xxx` in `docs/KNOWN_ISSUES.md` and list it under the spec's *Known deviations* |
| Code differs, and the **code is right** (intended change) | the spec must go back to `draft` and be edited. Don't silently rewrite an approved or implemented spec; ask the user |
| Behavior exists in code with no AC | propose a new AC (draft) to the user |

Also check:
- Every REST route in `server/*/urls.py` appears in `docs/contracts/rest-api.md`.
- Every WS `type` string sent or handled (`grep -rn '"type": "' server/channel server/chats server/background_worker`) appears in `docs/contracts/websocket-events.md`.

## Step 4 — Update metadata
For each spec you fully verified, set `last_verified: <today>` in frontmatter. Don't change `status` except:
- `approved` → `implemented`, when all ACs are met (or deviations are logged) and the user agrees.

## Step 5 — Report
Summarize per spec: `ID | status | ACs ok/total | new BUGs | actions needed`. Re-run Step 1 and confirm it exits 0.
