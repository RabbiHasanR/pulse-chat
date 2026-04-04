---
name: add-endpoint
description: Guided workflow for adding a new REST API endpoint to Pulse Chat — serializer, service layer, APIView, URL, test. Follows project patterns step by step.
---

Add a new REST endpoint. Follow steps in order. Wait for approval at each step before writing code.

$ARGUMENTS: what the endpoint does (e.g. "edit a chat message", "block a contact")

## Step 1 — Clarify
Ask if not already clear:
- Which app? (`users` / `chats` — pick by domain)
- HTTP method? (GET / POST / PATCH / DELETE)
- Which models does it read/write?
- Auth required? (yes — all endpoints use JWT)

## Step 2 — Model Check
Read `<app>/models.py`. Does this need a new field?
If yes: propose field + migration. Wait for approval before adding.

## Step 3 — Serializer
Check `<app>/serializers.py` for reusable serializers.
Design request + response serializer. Show, wait for approval.

## Step 4 — Service Layer
Business logic belongs in `<app>/services.py`, never in the view.
Read the existing service class to match patterns.
Add the method. Show, wait for approval.

## Step 5 — View
Read `<app>/views.py` for existing patterns. Then:
- Subclass `APIView` (never ViewSets)
- `permission_classes = [IsAuthenticated]`
- Call service; no logic in view body
- Return `success_response()` / `error_response()` from `utils/response.py`

Show, wait for approval.

## Step 6 — URL
Add route to `<app>/urls.py`. Rules: kebab-case, no trailing slash.
Show, wait for approval.

## Step 7 — Test
File: `tests/<app>/test_views.py`. Use `auth_client` fixture. URL from `tests/constants.py`.
Cover: happy path + missing/invalid fields + unauthorized.
Show, wait for approval before writing.

## Step 8 — Validate
Run `django-pattern-checker` agent on the new files.
Run `/test tests/<app>/` to confirm tests pass.
