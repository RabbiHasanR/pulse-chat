---
name: design
description: >-
  Guided feature planning and architecture discussion for Pulse Chat — maps ideas
  to existing data models, evaluate approaches against project constraints,
  and recommends an implementation sequence before drafting a specification.
---

# design — Feature Planning & Architecture Evaluation

**Input:** Feature idea or architectural challenge (e.g. "message reactions", "group chats", "voice calling").

## Step 1 — Clarify the Requirements
Ask clarifying questions to understand:
- What problem does this solve for users?
- What are the performance, concurrency, or real-time constraints?
- Does it modify any existing communication flows?

## Step 2 — Map to Existing Components
Identify impacted layers:
- **Models**: Which models need modification or creation (`ChatUser`, `Contact`, `Conversation`, `ChatMessage`, `MediaAsset`)?
- **Real-Time**: Are new inbound/outbound WebSocket events needed? (Review [docs/contracts/websocket-events.md](../../../docs/contracts/websocket-events.md)).
- **Queues**: Is heavy or background computation required? (Review [docs/architecture/media-pipeline.md](../../../docs/architecture/media-pipeline.md)).
- **Redis State**: Does it require new presence or viewing tracking keys in `RedisKeys`?
- **APIs**: What REST endpoints are required? (Review [docs/contracts/rest-api.md](../../../docs/contracts/rest-api.md)).

## Step 3 — Present Approaches & Trade-Offs
Offer 2–3 architectural approaches with:
- Complexity vs performance trade-offs.
- Comparison against existing project patterns.
- Any anti-pattern risks (e.g. non-cursor pagination, unbounded DB locks).

## Step 4 — Check Anti-Patterns & ADRs
Verify against every ADR in `docs/adr/` and rules in `server/AGENTS.md`.

## Step 5 — Recommend & Propose Next Steps
Summarize the recommended approach. Suggest creating a draft specification using `spec-write`:
*"If this design looks good, we can run `spec-write` to formally draft the specification and acceptance criteria."*
