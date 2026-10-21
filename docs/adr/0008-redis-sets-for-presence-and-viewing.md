# ADR-0008: Redis Sets for Real-Time Presence and Active Chat Viewing

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** RT-001, RT-002, RT-003

## Context
A real-time messaging application must track:
1. When a user is globally online (across all devices).
2. Which conversation a specific client tab currently has focused (for instant blue-tick "seen" receipt generation).
3. Which other users should receive presence updates when a user connects or disconnects.

Storing these high-frequency ephemeral state changes in PostgreSQL causes severe write amplification, transaction lock contention, and high I/O churn.

## Decision
All real-time presence and viewing states are managed in Redis Set data structures via `utils/redis_client.py`:
- `online_users`: Global set of active user IDs.
- `user:{user_id}:connections`: Set of active WebSocket `channel_name`s for `user_id`. When cardinality transitions from 0 to 1, user is marked online; when transitioning from 1 to 0, user is marked offline.
- `user:{user_id}:viewing:{partner_id}`: Set of socket connection names currently viewing `partner_id`. If `SCARD > 0`, incoming messages from `partner_id` are automatically marked `seen` immediately upon arrival.
- `user:{target_id}:presence_audience`: Set of observer user IDs interested in `target_id`'s presence changes, avoiding broadcast fan-out to the entire database.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Database column `is_online` on `ChatUser` | Standard relational querying | Massive write churn; stale state on ungraceful worker crashes or network drops. |
| Periodic client polling / heartbeat timestamps | No persistent sets | High polling query overhead; delayed presence awareness. |

## Consequences
**Positive**
- In-memory $O(1)$ set lookups and atomic mutations (`SADD`, `SREM`, `SCARD`).
- Decouples volatile connection state completely from durable relational storage.

**Rules this creates**
- Raw Redis keys must never be constructed directly in application views or consumers; all keys must be accessed via `RedisKeys` static methods.
