---
name: design
description: Guided feature planning and architecture discussion for Pulse Chat — maps ideas to existing components, evaluates approaches against project constraints, recommends implementation sequence.
---

Plan a new feature or evaluate an approach in the context of this project.

$ARGUMENTS: the feature or idea (e.g. "add message reactions", "group chat", "push notifications")

## Step 1 — Understand the Idea
Ask clarifying questions if needed:
- What problem does this solve for users?
- Any constraints (performance, real-time, scale)?
- Any existing behaviour it changes?

## Step 2 — Map to Existing Components
Identify which parts of the codebase are involved:
- **Models** — which to extend or create? (`ChatMessage`, `Conversation`, `MediaAsset`, `ChatUser`, `Contact`)
- **Queues** — any async work? (`default`, `image_queue`, `audio_queue`, `file_queue`, `video_queue`)
- **WebSocket events** — new events needed? (inbound: client→server; outbound: server→client)
- **Redis keys** — new presence/state tracking via `RedisKeys`?
- **APIs** — new endpoints in `users` or `chats`?

## Step 3 — Present Approaches
Offer 2–3 implementation approaches with:
- Trade-offs (complexity, performance, correctness)
- Which project patterns each approach uses or extends

## Step 4 — Flag Constraints
Check the proposed approach against every rule in the **Anti-Patterns** section of `server/.claude/CLAUDE.md`. Flag any that apply.

## Step 5 — Recommend & Sequence
State the recommended approach and why.
Show the implementation order: migrations → services → API → WebSocket → tasks → tests.

Wait for agreement before any code is written.
