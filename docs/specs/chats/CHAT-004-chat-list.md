---
id: CHAT-004
title: Conversations Inbox & Unread Summaries
status: implemented
area: chats
owners: [rabbihasan]
code:
  - server/chats/views.py
  - server/chats/models.py
  - server/chats/serializers.py
  - server/chats/pagination.py
  - server/utils/redis_client.py
contracts:
  - docs/contracts/rest-api.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0002-cursor-based-pagination.md
  - docs/adr/0006-denormalized-conversation-unread-counters.md
  - docs/adr/0008-redis-sets-for-presence-and-viewing.md
last_verified: 2026-10-21
---

# CHAT-004 — Conversations Inbox & Unread Summaries

## 1. Summary
Retrieves the authenticated user's active conversations sorted by most recent activity (`-updated_at`) using cursor pagination. Supplies denormalized message previews, participant profile cards, unread message badges, and live online presence statuses.

## 2. Context
The conversations inbox is the primary screen loaded upon opening the chat app. It requires instant render speeds, zero $O(N)$ join queries, and real-time online status indicators.

## 3. Scope
**In scope**
- Querying `Conversation` rows where user is `participant_1` or `participant_2`.
- Cursor pagination on `-updated_at`.
- Annotating the partner user ID.
- Batch fetching user profile objects to avoid N+1 queries.
- Querying Redis via `ChatRedisService.subscribe_and_get_presences` to populate `is_online` and subscribe caller to partner presences.
- Extracting unread badge count from `unread_counts[str(user_id)]`.

**Out of scope**
- Full message thread contents (covered in [CHAT-005](CHAT-005-message-history.md)).

## 4. Behavior
Client calls `GET /api/chat/list/?cursor=...`. Server filters conversations, applies `ChatListCursorPagination` (page size 20), queries Redis pipeline for partner online presence and audience subscription, and returns serialized conversation summaries.

## 5. Interface
- **Endpoint**: `GET /api/chat/list/` (Authenticated)
- **Response**: HTTP 200 OK cursor paginated
  ```json
  {
    "success": true,
    "message": "Chats retrieved successfully",
    "data": {
      "conversations": [
        {
          "id": 24,
          "updated_at": "2026-10-21T13:00:00Z",
          "last_message_time": "2026-10-21T13:00:00Z",
          "partner": {
            "id": 102,
            "email": "jane@example.com",
            "full_name": "Jane Doe",
            "avatar_url": null
          },
          "is_online": true,
          "last_message": "Hey there!",
          "unread_count": 2
        }
      ],
      "next": null,
      "previous": null
    }
  }
  ```

## 6. Acceptance criteria
- **AC1** Given an authenticated user, calling `GET /api/chat/list/` returns cursor-paginated conversations where the user is either participant 1 or participant 2.
- **AC2** Conversations are ordered by `-updated_at` (most recently active first).
- **AC3** Each item includes serialized partner details, `is_online` status matching Redis `online_users`, and `unread_count` extracted from `unread_counts[user_id]`.
- **AC4** `last_message` formats text truncated to 60 characters, or emoji-prefixed labels (`📷 Photo`, `🎥 Video`, `🎤 Audio`, `📁 File`, `🖼️ Album`).
- **AC5** When the conversation list is retrieved, the caller is registered in each partner's `presence_audience` set in Redis.

## 7. Non-functional requirements
- Page size: 20 conversations. Cursor query utilizes compound DB indexes `(participant_1, -updated_at)` and `(participant_2, -updated_at)`.

## 8. Known deviations
- None.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
