---
id: USR-003
title: Explore Users Directory
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
  - docs/adr/0002-cursor-based-pagination.md
last_verified: 2026-10-21
---

# USR-003 — Explore Users Directory

## 1. Summary
Provides a searchable or browsable public directory of all registered users in Pulse Chat (excluding the requesting user themselves), paginated using cursor pagination.

## 2. Context
Allows new users to discover other accounts to add to their contacts and start new conversations with.

## 3. Scope
**In scope**
- Returning a paginated list of `ChatUser` records excluding `request.user.id`.
- Ordered by `-date_joined`.

**Out of scope**
- Complex text search filtering (planned for future search service).

## 4. Behavior
Client calls `GET /api/auth/explore-users/?cursor=...`. Server queries `ChatUser.objects.exclude(id=request.user.id).order_by('-date_joined')`, paginates with `UserCursorPagination`, and returns serialized user items.

## 5. Interface
- **Endpoint**: `GET /api/auth/explore-users/` (Authenticated)
- **Response**: HTTP 200 OK cursor paginated
  ```json
  {
    "success": true,
    "message": "Users retrieved successfully",
    "data": {
      "users": [
        {
          "id": 102,
          "username": "janedoe",
          "email": "jane@example.com",
          "full_name": "Jane Doe",
          "avatar_url": "https://chat-bucket.s3.amazonaws.com/avatars/active/user_102.webp"
        }
      ],
      "next": null,
      "previous": null
    }
  }
  ```

## 6. Acceptance criteria
- **AC1** Given an authenticated user, calling `explore-users` returns cursor-paginated users with ID, username, email, full_name, and avatar_url.
- **AC2** The returned results must never include the authenticated caller's own account.
- **AC3** Ordering is reverse chronological by account creation (`-date_joined`).
- **AC4** Given an unauthenticated caller, HTTP 401 Unauthorized is returned.

## 7. Non-functional requirements
- Cursor pagination on indexed timestamp ensures $O(1)$ database seek time.

## 8. Known deviations
- None.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
