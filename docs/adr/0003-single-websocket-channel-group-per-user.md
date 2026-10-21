# ADR-0003: Single WebSocket Channel Group per User (`user_{user_id}`)

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** RT-001, RT-002, RT-003, CHAT-001

## Context
In chat architectures, clients need real-time delivery of messages across hundreds of different conversations, receipts, and presence updates. A common naive pattern is creating a distinct channel layer group for every conversation (e.g., `chat_{conversation_id}`) and requiring clients to dynamically join and leave groups as they navigate the UI. This creates extreme Redis Pub/Sub overhead, race conditions during group subscription, and high complexity when syncing multiple devices belonging to the same user.

## Decision
Pulse Chat assigns **exactly one channel layer group per user**: `user_{user_id}`.
- All outbound events destined for a user (new messages, progress updates, delivery receipts, read receipts, and presence alerts) are addressed to `user_{target_user_id}`.
- Every client connection (tab, browser window, mobile device) for `user_id` joins `user_{user_id}` upon handshake.
- Writes (sending messages) always occur over REST (`POST /api/chat/send/`), which handles DB transactions atomically, and then broadcasts to both sender and receiver groups (`user_{sender}` and `user_{receiver}`).

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Per-conversation groups (`conversation_{id}`) | Matches chat room mental model | Requires massive group join/leave churn; fails to sync the sender's other open tabs. |
| Inbound socket message sending (sending over WS) | Avoids HTTP round-trip | Hard to enforce transactional atomicity, rate limiting, and S3 multipart orchestration. |

## Consequences
**Positive**
- Perfect multi-device and multi-tab synchronization out of the box.
- Minimal Redis pub/sub memory footprint and zero subscription thrashing.
- REST writes ensure robust request validation, transactional integrity, and idempotency.

**Rules this creates**
- Messages are never transmitted from client to server over WebSocket. WS is strictly a push notification pipe from server to client.
- Outbound server broadcasts must route through `user_{id}` channel groups.
