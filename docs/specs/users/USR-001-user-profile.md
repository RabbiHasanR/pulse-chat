---
id: USR-001
title: User Profile
status: implemented
area: users
owners: [rabbihasan]
code:
  - server/users/views.py
  - server/users/models.py
  - server/users/serializers.py
contracts:
  - docs/contracts/rest-api.md
adrs:
  - docs/adr/0001-spec-driven-development.md
last_verified: 2026-10-21
---

# USR-001 — User Profile

## 1. Summary
Provides the authenticated user with their own account profile details including unique ID, username, email, full name, and avatar URL.

## 2. Context
Frontend clients require a dedicated profile endpoint upon app boot to initialize client-side user session context.

## 3. Scope
**In scope**
- Retrieving the currently authenticated user's profile (`GET /api/auth/me/`).
- Resolving the full S3 avatar URL via model property.

**Out of scope**
- Editing profile text (name/email updates).

## 4. Behavior
Client sends `GET /api/auth/me/` with a valid Bearer token. Server serializes `request.user` via `UserSerializer` and returns data inside standard success envelope.

## 5. Interface
- **Endpoint**: `GET /api/auth/me/` (Authenticated)
- **Response**: HTTP 200 OK
  ```json
  {
    "success": true,
    "message": "User details retrieved successfully",
    "data": {
      "id": 101,
      "username": "johndoe",
      "email": "user@example.com",
      "full_name": "John Doe",
      "avatar_url": "https://chat-bucket.s3.amazonaws.com/avatars/active/user_101_abc.webp"
    }
  }
  ```

## 6. Acceptance criteria
- **AC1** Given an authenticated user, calling `GET /api/auth/me/` returns HTTP 200 with the caller's ID, username, email, full_name, and avatar_url.
- **AC2** Given a user with no avatar uploaded, `avatar_url` is `null`.
- **AC3** Given an unauthenticated caller, HTTP 401 Unauthorized is returned.

## 7. Non-functional requirements
- Sub-5ms database lookup by primary key (`request.user.id`).

## 8. Known deviations
- None.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
