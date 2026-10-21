---
name: spec-implement
description: >-
  Guided workflow for implementing an approved specification in Pulse Chat.
  Verifies the spec is in approved status, implements the feature across models,
  services, views, tasks, and WebSocket layers, updates contracts, and marks the
  spec as implemented.
---

# spec-implement — Implement an Approved Specification

**Input:** Spec ID (e.g. `CHAT-002`, `MEDIA-003`).

## Step 1 — Verify Status Gate
1. Read the specification file: `docs/specs/<area>/<ID>-*.md`.
2. Inspect the frontmatter:
   - If `status == "approved"`: Proceed to Step 2.
   - If `status == "draft"`: **STOP**. Inform the user that the specification must be reviewed and approved by the maintainer before code can be written. Offer to review the spec.
   - If `status == "implemented"`: Ask if this is a bug fix or a specification revision.

## Step 2 — Plan from Acceptance Criteria
Map each acceptance criterion (`<ID>.AC<n>`) to the code components that need to be created or modified:
1. **Database Schema**: Do any models need fields or indexes? (Write migration).
2. **Service Layer**: Implement business logic in `<app>/services.py`.
3. **API / Serializers**: Implement request/response serialization and `APIView` controller.
4. **WebSocket**: If real-time events are required, implement consumer handlers or dispatch helpers.
5. **Background Tasks**: If async processing is needed, implement Celery tasks with proper queue routing.

## Step 3 — Implementation
Execute the plan step-by-step:
- Follow all patterns from `server/AGENTS.md` (no logic in views, cursor pagination, `RedisKeys`, `utils/s3.py`).
- Implement the acceptance criteria in sequence.

## Step 4 — Contract Synchronization
- If REST endpoints were added or changed: Update `docs/contracts/rest-api.md`.
- If WebSocket events were added or changed: Update `docs/contracts/websocket-events.md`.
- Ensure all relative links and route paths match.

## Step 5 — Update Spec Status
1. In the specification frontmatter:
   - Set `status: implemented`.
   - Set `last_verified: <today_date>`.
   - Update `code:` list with all touched files.
2. In `docs/specs/README.md`:
   - Update the status column to `implemented`.
3. In `## 10. Changelog`:
   - Record implementation entry: `| <today> | Implemented in code | <author> |`.

## Step 6 — Validate
Run the validation script:
```bash
python3 .agents/skills/spec-sync/scripts/check_specs.py
```
Confirm zero errors.
