# Specifications

Specs are the **source of truth for behavior** in Pulse Chat. Code implements specs; tests verify specs. When code and spec disagree, the disagreement is either a bug (log it in [KNOWN_ISSUES.md](../KNOWN_ISSUES.md)) or the spec must be updated through the normal lifecycle.

## Golden rules (humans and AI agents)

1. **Read before you change.** Before changing behavior, find the spec that governs the code (`code:` list in frontmatter) and read it.
2. **No behavior change without an `approved` spec.** Refactors that keep behavior identical don't need a spec change, but they must keep `code:` paths valid.
3. **Agents never self-approve.** An agent may write or edit `draft` specs. Only the maintainer approves. An agent may set `status: approved` **only after the maintainer explicitly says so in the conversation**, and must record that in the spec changelog.
4. **Same change, same commit.** Code, spec status, contracts ([websocket-events.md](../contracts/websocket-events.md), [rest-api.md](../contracts/rest-api.md)), and `last_verified` are updated together.
5. **Validate.** Run `python3 .agents/skills/spec-sync/scripts/check_specs.py` before committing.

## Lifecycle

```mermaid
stateDiagram-v2
    [*] --> draft: spec-write
    draft --> draft: review / edit
    draft --> approved: maintainer approves
    approved --> implemented: spec-implement (code + contracts updated)
    implemented --> draft: change requested (new revision)
    implemented --> deprecated: feature removed
    deprecated --> [*]
```

| Status | Meaning | Who sets it |
| --- | --- | --- |
| `draft` | Proposed; may change freely | Anyone / agent |
| `approved` | Agreed; implementation may start | Maintainer (or agent on explicit maintainer instruction) |
| `implemented` | Code matches the spec (except listed known deviations) | Implementer after verification |
| `deprecated` | Behavior removed or superseded | Maintainer |

**Changing an implemented feature:** bump the spec back to `draft`, edit it, and record the reason in the changelog. Once it's approved again, implement it and set it back to `implemented`.

## IDs

| Thing | Format | Example |
| --- | --- | --- |
| Spec | `<AREA>-<NNN>` | `CHAT-001` |
| Acceptance criterion | `<SPEC>.AC<n>` | `CHAT-001.AC3` |
| Known issue | `BUG-<NNN>` | `BUG-004` |
| Decision record | `ADR-<NNNN>` | `ADR-0003` |

Areas: `AUTH` (authentication), `USR` (users, contacts, avatar), `CHAT` (conversations, messages), `RT` (realtime/WebSocket), `MEDIA` (upload processing), `OPS` (infra, deployment, tooling).

- IDs are **never reused**. Deprecated specs keep their ID.
- File name: `docs/specs/<area>/<ID>-<kebab-title>.md`.
- Acceptance criteria are written as `- **AC<n>** Given … When … Then …`. Don't renumber existing ACs; append new ones. Mark removed ones ~~struck through~~ with a note.
- In tests (TDD phase), reference ACs in the docstring: `"""CHAT-001.AC3"""`.

## Writing a spec

Use the [template](_TEMPLATE.md) or the `spec-write` skill (`.agents/skills/spec-write/SKILL.md`). A good spec:

- describes **observable behavior** (API responses, WS events, DB state, side effects), not implementation trivia
- has ACs that are each independently testable
- lists every file it governs in `code:` so agents can find it from the code side
- links the relevant contracts and ADRs

**As-built specs** document existing behavior. If current behavior is wrong, the AC describes the **correct** behavior, and the gap is listed under *Known deviations* with a `BUG-xxx` link, so the TDD phase can turn each deviation into a failing test.

## Index

<!-- Keep this table in sync with the files in this directory. check_specs.py verifies it. -->

| ID | Title | Status | Area |
| --- | --- | --- | --- |
| [AUTH-001](auth/AUTH-001-user-registration.md) | User Registration | implemented | auth |
| [AUTH-002](auth/AUTH-002-otp-login.md) | Passwordless OTP Login | implemented | auth |
| [AUTH-003](auth/AUTH-003-client-bound-jwt.md) | Client-Bound JWT Tokens & Refresh | implemented | auth |
| [AUTH-004](auth/AUTH-004-user-logout.md) | User Logout & Token Blacklisting | implemented | auth |
| [USR-001](users/USR-001-user-profile.md) | User Profile | implemented | users |
| [USR-002](users/USR-002-contacts-management.md) | Contacts Management & Presence Subscription | implemented | users |
| [USR-003](users/USR-003-explore-users.md) | Explore Users Directory | implemented | users |
| [USR-004](users/USR-004-avatar-upload.md) | User Avatar Upload & Confirmation | implemented | users |
| [CHAT-001](chats/CHAT-001-send-text-message.md) | Send Text Message | implemented | chats |
| [CHAT-002](chats/CHAT-002-media-message-upload.md) | Media Message & Direct-to-S3 Upload Pipeline | implemented | chats |
| [CHAT-003](chats/CHAT-003-forward-message.md) | Bulk Message Forwarding & Zero-Copy S3 Cloning | implemented | chats |
| [CHAT-004](chats/CHAT-004-chat-list.md) | Conversations Inbox & Unread Summaries | implemented | chats |
| [CHAT-005](chats/CHAT-005-message-history.md) | Message History & Read Receipt Generation | implemented | chats |
| [RT-001](realtime/RT-001-websocket-connection.md) | WebSocket Connection & Multi-Device Presence | implemented | realtime |
| [RT-002](realtime/RT-002-chat-view-and-typing.md) | Active Chat Viewing Tracking & Typing Indicators | implemented | realtime |
| [RT-003](realtime/RT-003-delivery-read-receipts.md) | Delivery & Read Receipts Notification Engine | implemented | realtime |
| [MEDIA-001](media/MEDIA-001-image-processing.md) | Asynchronous Image Processing Pipeline | implemented | media |
| [MEDIA-002](media/MEDIA-002-video-hls-processing.md) | Asynchronous Video HLS Transcoding & Checkpointing | implemented | media |
| [MEDIA-003](media/MEDIA-003-audio-processing.md) | Asynchronous Audio Transcoding & Waveform Extraction | implemented | media |
| [MEDIA-004](media/MEDIA-004-file-document-processing.md) | File & Document Attachment Processing | implemented | media |
| [MEDIA-005](media/MEDIA-005-asset-finalization-and-cleanup.md) | Asset Finalization, Failure Routing & Stuck Sweeping | implemented | media |
