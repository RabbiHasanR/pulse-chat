---
name: spec-write
description: >-
  Guided workflow for drafting a new feature specification or modifying an existing spec.
  Ensures consistent YAML frontmatter, valid area, numbered acceptance criteria,
  governing code paths, and registers the spec in docs/specs/README.md.
  Leaves the spec in draft status for maintainer approval.
---

# spec-write — Draft or Revise a Specification

**Input:** Feature description or change request (e.g. "message reactions", "delete message for everyone", "voice message speed playback").

## Step 1 — Clarify Domain & Scope
1. Which area does this feature belong to?
   - `auth`: Authentication, OTP, JWT, security tokens.
   - `users`: User profiles, contacts, avatars.
   - `chats`: Conversations, messaging, attachments, inbox.
   - `realtime`: WebSocket events, presence, receipts.
   - `media`: Transcoding, audio/video pipelines, file parsing.
   - `ops`: Infra, container configuration, background scheduling.
2. Determine the next available ID:
   Check existing IDs in `docs/specs/<area>/` and pick `<AREA>-<NEXT_3_DIGIT_NUMBER>`.
3. Check existing ADRs in `docs/adr/` to ensure the proposal adheres to project architectural decisions.

## Step 2 — Draft the Specification File
Create `docs/specs/<area>/<ID>-<kebab-title>.md` following `docs/specs/_TEMPLATE.md`:
- Set `status: draft`.
- Set `last_verified: <today_date>` (YYYY-MM-DD).
- List expected or existing files in `code:`.
- Add testable acceptance criteria with format:
  `- **AC1** Given <precondition>, when <action>, then <expected outcome>.`
- If modifying an existing feature, mark status `draft` and document the revision reason in `## 10. Changelog`.

## Step 3 — Register in Index
Add the new spec row into `docs/specs/README.md`:
```markdown
| [<ID>](<area>/<FILE_NAME>.md) | <Title> | draft | <area> |
```

## Step 4 — Validate
Run the spec integrity validator:
```bash
python3 .agents/skills/spec-sync/scripts/check_specs.py
```
Fix any structural or link errors.

## Step 5 — Request Maintainer Review
Present the drafted specification to the user.
**CRITICAL RULE**: Do NOT change `status` to `approved` on your own. Explicitly ask the user:
*"Please review the drafted specification. Let me know if you would like any changes or if you approve it."*
