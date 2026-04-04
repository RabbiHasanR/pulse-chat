---
name: add-ws-event
description: Guided workflow for adding a new WebSocket event to Pulse Chat — consumer handler, ChatRedisService dispatch, test with WebsocketCommunicator.
---

Add a new WebSocket event. Follow steps in order. Wait for approval at each step.

$ARGUMENTS: what the event does (e.g. "message reaction", "user typing in group")

## Step 1 — Clarify
- Direction: client→server, server→client, or bidirectional?
- Payload: what data does it carry?
- Audience: specific user / conversation participants / broadcast?

## Step 2 — Define Event Schema
Design the payload (snake_case type):
```json
{ "type": "<event_type>", "data": { ... } }
```
Show, wait for approval.

## Step 3 — Consumer Handler (client→server)
Read `channel/consumers.py` for existing handler patterns.
- Add `_handle_<event>(self, data)` method to `UserSocketConsumer`
- Add dispatch case in `receive()`
Show, wait for approval.

## Step 4 — Server Dispatch (server→client)
Read `utils/redis_client.py` — `ChatRedisService` methods.
- User-targeted: `channel_layer.group_send(f"user_{user_id}", payload)`
- Celery high-frequency: `_send_socket_update_directly()`
- Add `<event_type>` handler method in consumer that calls `self.send()`
Show, wait for approval.

## Step 5 — Test
File: `tests/channel/test_user_socket_consumer.py`.
Use `WebsocketCommunicator` + `patch_redis` + `fake_redis` + `@pytest.mark.asyncio`.
Use `helpers` fixture for `recv_type()` / `recv_until()`.
Show, wait for approval.

## Step 6 — Document
Add event to the WebSocket events section in `server/.claude/CLAUDE.md`.
