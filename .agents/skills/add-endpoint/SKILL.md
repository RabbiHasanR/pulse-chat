---
name: add-endpoint
description: >-
  Guided workflow for adding a new REST API endpoint to Pulse Chat — verifies
  the approved specification, serializer, service layer, APIView, URL with
  trailing slash, and tests. Follows project patterns step by step.
---

# add-endpoint — Add a New REST Endpoint

**Input:** Endpoint description or spec ID (e.g. "edit a chat message", "CHAT-006").

## Step 0 — Spec Check
1. Ensure the feature is defined in an `approved` spec in `docs/specs/`.
2. If no approved spec exists, invoke `spec-write` first to draft the specification.

## Step 1 — Clarify Endpoint Details
Confirm:
- App: `users` or `chats`.
- HTTP method: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.
- Target URL path (kebab-case with trailing slash, e.g. `path('messages/<int:pk>/edit/', ...)`).
- Authentication: Required (`IsAuthenticated`) or unauthenticated.

## Step 2 — Model Check
Check `<app>/models.py`. Does the endpoint require schema changes?
If yes, draft migration and review with `review-migration` before applying.

## Step 3 — Serializer
In `<app>/serializers.py`:
- Design request input serializer (`<Action>InSerializer`).
- Design response serializer if returning complex entity models.
- Validate types, limits, and sanitization.

## Step 4 — Service Layer
Business logic belongs strictly in `<app>/services.py`, never in views.
- Add or extend service method.
- Wrap mutations in `@transaction.atomic` if modifying multiple records or denormalized counters.

## Step 5 — View Controller
In `<app>/views.py`:
- Subclass `rest_framework.views.APIView` (never ViewSet).
- Set `permission_classes = [IsAuthenticated]`.
- Validate with serializer: return `error_response(...)` on invalid data.
- Call service method.
- Return `success_response(...)` with proper HTTP status code (`200` or `201`).

## Step 6 — URL Configuration
In `<app>/urls.py`:
- Add route with a **trailing slash** (e.g. `path('my-endpoint/', MyView.as_view(), name='my-endpoint')`).

## Step 7 — Contract & Spec Synchronization
1. Add endpoint entry to [docs/contracts/rest-api.md](../../../docs/contracts/rest-api.md).
2. Update the governing spec's `code:` frontmatter.
3. Run `python3 .agents/skills/spec-sync/scripts/check_specs.py`.
