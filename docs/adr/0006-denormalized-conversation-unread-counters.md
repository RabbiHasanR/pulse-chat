# ADR-0006: Denormalized Conversation Fields and JSON Unread Counters

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** CHAT-001, CHAT-004, CHAT-005

## Context
When loading the main chat inbox view, a client needs to show:
1. The list of active conversations, ordered by the most recent message timestamp.
2. A short preview string of the last message (e.g. "Hey!", "📷 Photo", "🎤 Audio").
3. The count of unread messages for the logged-in user.

Computing these values dynamically using relational joins and `COUNT(*)` over `ChatMessage` across hundreds of conversations creates significant database CPU load and makes cursor pagination sluggish.

## Decision
We denormalize recent message metadata and unread counts directly onto the `Conversation` model:
- `last_message_content`: Truncated preview text or media indicator.
- `last_message_type`: Message type enum.
- `last_message_time`: Timestamp of last activity.
- `unread_counts`: A JSONField mapping string participant user IDs to integer badge counts (e.g. `{"101": 0, "102": 4}`).
- `participant_1_id` and `participant_2_id` are strictly ordered such that `p1 < p2`.
- When a message is sent, `ChatService` updates these fields atomically. When a user reads a conversation, their specific key in `unread_counts` is reset to 0.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Relational aggregate queries (`Subquery`, `COUNT`) | Strictly normalized schema | Expensive `COUNT(*)` and `MAX(created_at)` scans on every conversation load. |
| Separate `UserConversationState` table | Normalized per-user counters | Requires additional join tables and multi-table transactions on every message sent. |

## Consequences
**Positive**
- Rendering the user's inbox requires a single indexed query on `Conversation` with zero joins on `ChatMessage`.
- Sub-millisecond inbox load times.

**Negative / trade-offs**
- Denormalized state must be kept in sync by all message write paths (`send_text_message`, `initialize_media_message`, `forward_message_batch`).

**Rules this creates**
- All writes to `Conversation` must enforce participant sorting (`p1 < p2`).
- Updates to `unread_counts` must be handled inside database transactions.
