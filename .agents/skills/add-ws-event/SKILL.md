---
name: add-ws-event
description: >-
  Guided workflow for adding a new WebSocket event to Pulse Chat — verifies
  the approved specification, consumer inbound handler, channel layer outbound
  dispatch to user_{id}, and contract documentation.
---

# add-ws-event — Add a New WebSocket Event

**Input:** Event name or description (e.g. "message reaction", "chat audio recording status").

## Step 0 — Spec Check
1. Ensure the event is specified in an `approved` spec under `docs/specs/realtime/` or related domain.
2. If none exists, invoke `spec-write` first.

## Step 1 — Clarify Event Direction & Payload
1. **Direction**:
   - Inbound (Client $\to$ Server).
   - Outbound (Server $\to$ Client).
2. **Payload Structure**:
   Use snake_case `type` string and structured JSON body:
   ```json
   { "type": "<event_type>", "data": { ... } }
   ```
3. **Audience**:
   Outbound messages target user channel groups: `user_{user_id}`.

## Step 2 — Inbound Handler (Client $\to$ Server)
If the event is sent by clients:
1. In `server/channel/consumers.py`:
   - Add dispatch branch inside `receive()` in `UserSocketConsumer`.
   - Implement `async def _handle_<event>(self, data: dict):`.
   - Validate payload fields.

## Step 3 — Outbound Dispatch (Server $\to$ Client)
1. For notifications triggered from REST services (`services.py`):
   ```python
   channel_layer = get_channel_layer()
   async_to_sync(channel_layer.group_send)(
       f"user_{target_user_id}",
       {
           "type": "forward_event",
           "payload": {
               "type": "<event_type>",
               "data": { ... }
           }
       }
   )
   ```
2. For high-frequency Celery worker notifications (e.g. progress meters):
   Use `_send_socket_update_directly(user_id, payload)` in `background_worker/chats/tasks.py`.

## Step 4 — Contract Synchronization
1. Document the event schema in [docs/contracts/websocket-events.md](../../../docs/contracts/websocket-events.md).
2. Update the governing spec and run `python3 .agents/skills/spec-sync/scripts/check_specs.py`.
