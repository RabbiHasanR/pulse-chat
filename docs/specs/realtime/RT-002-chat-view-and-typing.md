---
id: RT-002
title: Active Chat Viewing Tracking & Typing Indicators
status: implemented
area: realtime
owners: [rabbihasan]
code:
  - server/channel/consumers.py
  - server/utils/redis_client.py
contracts:
  - docs/contracts/websocket-events.md
adrs:
  - docs/adr/0001-spec-driven-development.md
  - docs/adr/0008-redis-sets-for-presence-and-viewing.md
last_verified: 2026-10-21
---

# RT-002 — Active Chat Viewing Tracking & Typing Indicators

## 1. Summary
Tracks which specific conversation a client socket has actively focused using Redis viewing sets (`user:{user_id}:viewing:{receiver_id}`), and proxies transient typing indicators (`chat_typing`) to the chat partner.

## 2. Context
Automatic generation of read receipts (blue ticks) requires the server to know when User B is actively looking at User A's thread when User A hits send. Real-time typing indicators inform users when the counterparty is composing a response.

## 3. Scope
**In scope**
- Handling `chat_open` event: adding socket `channel_name` to `user:{user}:viewing:{target}`.
- Handling `chat_close` event: removing socket from viewing set.
- Automatically cleaning viewing set on socket disconnect.
- Handling `chat_typing` event: forwarding event directly to `user_{receiver_id}` channel group.

**Out of scope**
- Typing indicator timeouts or debouncing (handled by client composition timer).

## 4. Behavior
- **Chat Open**: When a user selects a chat in the UI, the client sends `{"type": "chat_open", "receiver_id": 102}`. The socket's `channel_name` is added to `user:{user}:viewing:102` with 86400s TTL. If previously viewing another contact, the socket is removed from the old viewing set.
- **Chat Close**: When leaving the chat screen, client sends `{"type": "chat_close", "receiver_id": 102}`. The socket is removed from the set.
- **Typing Indicator**: Client sends `{"type": "chat_typing", "receiver_id": 102}`. Consumer forwards `{"type": "chat_typing", "data": {"sender_id": my_id, "receiver_id": 102}}` to `user_102`.

## 5. Interface
- **Inbound Events**:
  - Open: `{"type": "chat_open", "receiver_id": 102}`
  - Close: `{"type": "chat_close", "receiver_id": 102}`
  - Typing: `{"type": "chat_typing", "receiver_id": 102}`
- **Outbound Event**:
  - Typing: `{"type": "chat_typing", "data": {"sender_id": 101, "receiver_id": 102}}`

## 6. Acceptance criteria
- **AC1** Given an open socket, sending `chat_open` with `receiver_id` adds the current socket's `channel_name` to `user:{user.id}:viewing:{receiver_id}`.
- **AC2** If the socket was already viewing a different `receiver_id`, it is automatically removed from the previous viewing set when switching chats.
- **AC3** Sending `chat_close` removes the socket from the viewing set.
- **AC4** When a socket disconnects, any active viewing set membership held by that socket is cleaned up.
- **AC5** Sending `chat_typing` forwards the event to `user_{receiver_id}` with `sender_id` set to the authenticated caller's user ID.

## 7. Non-functional requirements
- Viewing set entries have an automated 24-hour expiration (`expire(key, 86400)`) to guard against orphaned keys.

## 8. Known deviations
- BUG-010: `_handle_chat_typing` forwards typing events to any arbitrary `receiver_id` without verifying an existing relationship or conversation.

## 9. Open questions
- None.

## 10. Changelog
| Date | Change | By |
| --- | --- | --- |
| 2026-10-21 | Initial as-built specification | Antigravity |
