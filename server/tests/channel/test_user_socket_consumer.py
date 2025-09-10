# tests/channel/test_user_socket_consumer.py

import asyncio
import importlib
import json
import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone
from asgiref.sync import sync_to_async

pytestmark = pytest.mark.django_db(transaction=True)

CONSUMER_MODULE = "channel.consumers"  # <-- make sure this matches your project


def app():
    mod = importlib.import_module(CONSUMER_MODULE)
    return mod.UserSocketConsumer.as_asgi()


async def connect_as(user, token="TKN", tab_id="tab-1", path=None):
    if path is None:
        path = f"/ws/user/?token={token}&tab_id={tab_id}"
    comm = WebsocketCommunicator(app(), path)
    # bypass auth middleware
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected is True
    return comm


# ------------------------- Basic auth/error paths ----------------------------

@pytest.mark.asyncio
async def test_anonymous_rejected_with_auth_error(patch_redis):
    comm = WebsocketCommunicator(app(), "/ws/user/?token=X&tab_id=A")
    # do NOT set scope["user"] -> anonymous
    connected, _ = await comm.connect()

    msg = json.loads(await comm.receive_from())
    assert msg["type"] == "auth_error"
    assert msg["success"] is False
    assert "Authentication failed" in msg["message"]

    # WebsocketCommunicator has no wait_closed(); use receive_nothing()
    # assert await comm.receive_nothing()
    
    # Your app likely queued a `websocket.close` event after the error message.
    close = await comm.receive_output(timeout=1.0)
    assert close["type"] == "websocket.close"
    # Optionally assert a specific close code if you set one, e.g. 4401
    # assert close.get("code") == 4401

    # Now the queue should be empty.
    assert await comm.receive_nothing()


@pytest.mark.asyncio
async def test_invalid_json_returns_error(user, patch_redis, helpers):
    recv_type = helpers["recv_type"]

    comm = await connect_as(user)
    await comm.send_to(text_data="{not-json")
    msg = await recv_type(comm, "error")
    assert msg["type"] == "error"
    assert msg["success"] is False
    assert "Invalid JSON" in msg["message"]
    await comm.disconnect()


@pytest.mark.asyncio
async def test_unknown_event_type(user, patch_redis, helpers):
    recv_type = helpers["recv_type"]

    comm = await connect_as(user)
    await comm.send_to(text_data=json.dumps({"type": "something_else"}))
    msg = await recv_type(comm, "error")
    assert msg["type"] == "error"
    assert "Unknown event type" in msg["message"]
    await comm.disconnect()


# ------------------------- Presence across tabs/devices ----------------------

@pytest.mark.asyncio
async def test_presence_multi_tabs_and_devices(user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    ONLINE = mod.UserSocketConsumer.ONLINE_USERS_SET
    tab_key = mod.UserSocketConsumer._active_tabs_key(user.id)

    # First tab (device A / browser A)
    comm_a1 = await connect_as(user, token="TA", tab_id="A1")
    assert user.id in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 1

    # Second tab same browser/device
    comm_a2 = await connect_as(user, token="TA", tab_id="A2")
    assert user.id in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 2

    # Another device/browser (different token)
    comm_b1 = await connect_as(user, token="TB", tab_id="B1")
    assert user.id in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 3

    # Close one tab; user must remain online and tab set decremented
    await comm_a2.disconnect()
    assert user.id in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 2

    # Close second tab; still one active (B1)
    await comm_a1.disconnect()
    assert user.id in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 1

    # Close last device → now offline, zero active tabs
    await comm_b1.disconnect()
    assert user.id not in patch_redis.sets.get(ONLINE, set())
    assert await patch_redis.scard(tab_key) == 0


# ------------------------- chat_message paths --------------------------------

@pytest.mark.asyncio
async def test_chat_message_success_echos_to_sender_and_receiver(user, another_user, patch_redis, helpers):
    recv_until = helpers["recv_until"]

    # Connect both sides to receive group events
    comm_sender = await connect_as(user, token="S1", tab_id="TS")
    comm_rcv = await connect_as(another_user, token="R1", tab_id="TR")

    await comm_sender.send_to(text_data=json.dumps({
        "type": "chat_message",
        "message": "hello world",
        "receiver_id": another_user.id
    }))

    # Each side should receive the same chat_message event
    s_evt = await recv_until(comm_sender, lambda m: m.get("type") == "chat_message")
    r_evt = await recv_until(comm_rcv,    lambda m: m.get("type") == "chat_message")

    for evt in (s_evt, r_evt):
        assert evt["success"] is True
        assert evt["data"]["content"] == "hello world"
        assert evt["data"]["sender_id"] == user.id
        assert evt["data"]["receiver_id"] == another_user.id
        assert evt["data"]["status"] in ("sent", "seen", "delivered")

    await comm_sender.disconnect()
    await comm_rcv.disconnect()


@pytest.mark.asyncio
async def test_chat_message_validation_error_missing_fields(user, patch_redis, helpers):
    recv_type = helpers["recv_type"]

    comm = await connect_as(user)
    await comm.send_to(text_data=json.dumps({"type": "chat_message", "message": "no receiver"}))
    msg = await recv_type(comm, "error")
    assert msg["type"] == "error"
    assert msg["success"] is False
    await comm.disconnect()


@pytest.mark.asyncio
async def test_chat_message_receiver_not_found(user, patch_redis, helpers):
    recv_type = helpers["recv_type"]

    comm = await connect_as(user)
    await comm.send_to(text_data=json.dumps({"type": "chat_message", "message": "hi", "receiver_id": 99999}))
    msg = await recv_type(comm, "error")
    assert msg["type"] == "error"
    assert "Receiver not found" in msg["message"]
    await comm.disconnect()


@pytest.mark.asyncio
async def test_chat_message_triggers_seen_when_receiver_online_and_viewing(user, another_user, patch_redis, helpers):
    # Receiver online & viewing -> sender should receive single 'message_status: seen'
    recv_until = helpers["recv_until"]
    mod = importlib.import_module(CONSUMER_MODULE)
    key_for_rcv_view = mod.UserSocketConsumer._active_thread_key(another_user.id, "R1", "TR")

    comm_sender = await connect_as(user, token="S1", tab_id="TS")
    comm_receiver = await connect_as(another_user, token="R1", tab_id="TR")

    # Simulate receiver currently viewing sender's thread by setting redis key
    await patch_redis.set(key_for_rcv_view, str(user.id), ex=30)

    await comm_sender.send_to(text_data=json.dumps({
        "type": "chat_message",
        "message": "seen please",
        "receiver_id": another_user.id
    }))

    # sender gets chat_message (echo) and then message_status seen
    status_evt = await recv_until(comm_sender, lambda m: m.get("type") == "message_status")
    assert status_evt["data"]["status"] == "seen"
    assert status_evt["data"]["sender_id"] == user.id
    assert status_evt["data"]["receiver_id"] == another_user.id

    await comm_sender.disconnect()
    await comm_receiver.disconnect()


@pytest.mark.asyncio
async def test_chat_message_triggers_unread_summary_when_receiver_online_not_viewing(user, another_user, patch_redis, helpers):
    # Receiver online but NOT viewing -> receiver should get 'chat_summary'
    recv_until = helpers["recv_until"]

    comm_sender = await connect_as(user, token="S2", tab_id="TS2")
    comm_receiver = await connect_as(another_user, token="R2", tab_id="TR2")

    await comm_sender.send_to(text_data=json.dumps({
        "type": "chat_message",
        "message": "ping",
        "receiver_id": another_user.id
    }))

    # Receiver will get the chat_message and (since online, not viewing) a chat_summary
    _ = await recv_until(comm_receiver, lambda m: m.get("type") == "chat_message")
    summary = await recv_until(comm_receiver, lambda m: m.get("type") == "chat_summary")

    assert summary["success"] is True
    assert summary["data"]["sender_id"] == user.id
    assert summary["data"]["receiver_id"] == another_user.id
    assert "unread_count" in summary["data"]
    assert "last_message" in summary["data"]

    await comm_sender.disconnect()
    await comm_receiver.disconnect()


# ------------------------- edit / delete -------------------------------------

@pytest.mark.asyncio
async def test_message_edit_and_delete_happy_path(user, another_user, patch_redis, helpers):
    recv_until = helpers["recv_until"]
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage

    comm_sender = await connect_as(user, token="ES", tab_id="E1")
    comm_receiver = await connect_as(another_user, token="ER", tab_id="E2")

    m = await sync_to_async(ChatMessage.objects.create)(
        sender_id=user.id,
        receiver_id=another_user.id,
        content="old content",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )

    await comm_sender.send_to(text_data=json.dumps({
        "type": "message_edit", "message_id": m.id, "new_content": "new content"
    }))

    s_edit = await recv_until(comm_sender,   lambda m: m.get("type") == "message_edit")
    r_edit = await recv_until(comm_receiver, lambda m: m.get("type") == "message_edit")
    assert s_edit["data"]["message_id"] == m.id
    assert s_edit["data"]["new_content"] == "new content"
    assert r_edit["data"]["message_id"] == m.id

    m.refresh_from_db()
    assert m.content == "new content"

    await comm_sender.send_to(text_data=json.dumps({
        "type": "message_delete", "message_id": m.id
    }))

    s_del = await recv_until(comm_sender,   lambda m: m.get("type") == "message_delete")
    r_del = await recv_until(comm_receiver, lambda m: m.get("type") == "message_delete")
    assert s_del["data"]["message_id"] == m.id
    assert r_del["data"]["message_id"] == m.id

    m.refresh_from_db()
    assert m.is_deleted is True

    await comm_sender.disconnect()
    await comm_receiver.disconnect()


@pytest.mark.asyncio
async def test_message_edit_unauthorized(user, another_user, patch_redis, helpers):
    recv_type = helpers["recv_type"]
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage

    # Message authored by other_user
    m = await sync_to_async(ChatMessage.objects.create)(
        sender_id=another_user.id,
        receiver_id=user.id,
        content="by other",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )

    comm = await connect_as(user)
    await comm.send_to(text_data=json.dumps({
        "type": "message_edit", "message_id": m.id, "new_content": "hijack"
    }))
    resp = await recv_type(comm, "error")
    assert resp["type"] == "error"
    assert "unauthorized" in resp["message"].lower()
    await comm.disconnect()


@pytest.mark.asyncio
async def test_message_delete_unauthorized(user, another_user, patch_redis, helpers):
    recv_type = helpers["recv_type"]
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage

    m = await sync_to_async(ChatMessage.objects.create)(
        sender_id=another_user.id,
        receiver_id=user.id,
        content="cannot delete",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )

    comm = await connect_as(user)
    await comm.send_to(text_data=json.dumps({
        "type": "message_delete", "message_id": m.id
    }))
    resp = await recv_type(comm, "error")
    assert resp["type"] == "error"
    assert "unauthorized" in resp["message"].lower()
    await comm.disconnect()


# ------------------------- chat_open / chat_close / heartbeat ----------------

@pytest.mark.asyncio
async def test_chat_open_marks_seen_sets_key_sends_batch_and_ack(user, another_user, patch_redis, helpers):
    recv_until = helpers["recv_until"]
    recv_type = helpers["recv_type"]
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage
    Consumer = mod.UserSocketConsumer

    # Seed: other_user -> user messages need to be marked seen
    m1 = await sync_to_async(ChatMessage.objects.create)(
        sender_id=another_user.id, receiver_id=user.id,
        content="hi", message_type="text",
        status="sent", created_at=timezone.now(),
    )
    m2 = await sync_to_async(ChatMessage.objects.create)(
        sender_id=another_user.id, receiver_id=user.id,
        content="yo", message_type="text",
        status="delivered", created_at=timezone.now(),
    )

    comm_user   = await connect_as(user, token="T3", tab_id="tA")
    comm_other  = await connect_as(another_user, token="T4", tab_id="tB")

    await comm_user.send_to(text_data=json.dumps({
        "type": "chat_open", "receiver_id": another_user.id
    }))

    # Receiver gets batch status
    batch = await recv_until(comm_other, lambda m: m.get("type") == "message_status_batch")
    ids = {d["message_id"] for d in batch["data"]}
    assert m1.id in ids and m2.id in ids

    # Opener gets ack (skip any presence_update etc.)
    ack = await recv_type(comm_user, "chat_open_ack")
    assert ack["type"] == "chat_open_ack"

    # Redis key set with expiry (STATICMETHOD: pass user_id, token, tab_id)
    key = Consumer._active_thread_key(user.id, "T3", "tA")
    assert key in patch_redis.kv and patch_redis.kv[key] == str(another_user.id)
    assert patch_redis.expiries.get(key) == 30

    # DB statuses updated
    m1.refresh_from_db(); m2.refresh_from_db()
    assert m1.status == "seen" and m2.status == "seen"

    await comm_user.disconnect()
    await comm_other.disconnect()


@pytest.mark.asyncio
async def test_chat_close_clears_key_and_ack(user, another_user, patch_redis, helpers):
    recv_type = helpers["recv_type"]
    mod = importlib.import_module(CONSUMER_MODULE)
    Consumer = mod.UserSocketConsumer

    comm = await connect_as(user, token="T5", tab_id="tC")
    key = Consumer._active_thread_key(user.id, "T5", "tC")
    await patch_redis.set(key, str(another_user.id), ex=30)

    await comm.send_to(text_data=json.dumps({
        "type": "chat_close", "receiver_id": another_user.id
    }))
    ack = await recv_type(comm, "chat_close_ack")
    assert ack["type"] == "chat_close_ack"
    assert key not in patch_redis.kv

    await comm.disconnect()


@pytest.mark.asyncio
async def test_heartbeat_extends_ttl(user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    Consumer = mod.UserSocketConsumer

    comm = await connect_as(user, token="HB", tab_id="h1")
    key = Consumer._active_thread_key(user.id, "HB", "h1")
    await patch_redis.set(key, "whatever", ex=5)

    await comm.send_to(text_data=json.dumps({"type": "heartbeat"}))
    # No return event; just verify expiry updated
    await asyncio.sleep(0.05)
    assert patch_redis.expiries.get(key) == 30

    await comm.disconnect()


# ------------------------- typing --------------------------------------------

@pytest.mark.asyncio
async def test_chat_typing_forwarded(user, another_user, patch_redis, helpers):
    recv_until = helpers["recv_until"]

    comm_sender = await connect_as(user, token="TT1", tab_id="t1")
    comm_rcv    = await connect_as(another_user, token="TT2", tab_id="t2")

    await comm_sender.send_to(text_data=json.dumps({
        "type": "chat_typing", "receiver_id": another_user.id
    }))
    evt = await recv_until(comm_rcv, lambda m: m.get("type") == "chat_typing")
    assert evt["success"] is True
    assert evt["data"]["sender_id"] == user.id
    assert evt["data"]["receiver_id"] == another_user.id

    await comm_sender.disconnect()
    await comm_rcv.disconnect()
