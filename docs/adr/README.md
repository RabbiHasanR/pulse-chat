# Architecture Decision Records

An ADR records **why** a significant technical decision was made, so future contributors and AI agents don't undo it by accident or re-argue it without new information.

## When to write one

Write one when a choice:
- constrains future code (e.g. "cursor pagination only")
- was chosen over reasonable alternatives
- would surprise a newcomer

Use the [template](_TEMPLATE.md) or the `adr-write` skill. Number sequentially and never reuse numbers. To change a decision, write a new ADR that **supersedes** the old one and update the old one's status.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-spec-driven-development.md) | Adopt Spec-Driven Development & Agent-Agnostic Governance | accepted |
| [0002](0002-cursor-based-pagination.md) | Enforce Cursor-Based Pagination for All List Endpoints | accepted |
| [0003](0003-single-websocket-channel-group-per-user.md) | Single WebSocket Channel Group per User (`user_{user_id}`) | accepted |
| [0004](0004-direct-to-s3-upload-architecture.md) | Direct-to-Cloud (S3) Upload Architecture | accepted |
| [0005](0005-passwordless-otp-and-client-bound-jwt.md) | Passwordless Email OTP & Client-Bound JWT Authentication | accepted |
| [0006](0006-denormalized-conversation-unread-counters.md) | Denormalized Conversation Fields and JSON Unread Counters | accepted |
| [0007](0007-celery-queue-isolation.md) | Celery Queue Isolation and CPU-Bounded Video Workers | accepted |
| [0008](0008-redis-sets-for-presence-and-viewing.md) | Redis Sets for Real-Time Presence and Active Chat Viewing | accepted |
