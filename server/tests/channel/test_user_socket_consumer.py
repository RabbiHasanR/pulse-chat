# tests/test_user_socket_consumer.py
import asyncio
import importlib
import json
import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

CONSUMER_MODULE = "yourapp.consumers"  # <-- CHANGE THIS
WS_PATH = "/ws/user/?token=TKN&tab_id=tab-1"


def _app():
    # Load the consumer directly to avoid routing complexity.
    mod = importlib.import_module(CONSUMER_MODULE)
    return mod.UserSocketConsumer.as_asgi()


async def _connect_as(user, path=WS_PATH):
    comm = WebsocketCommunicator(_app(), path)
    # Bypass auth middleware: set scope user directly
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected is True
    return comm


async def _recv_json(comm: WebsocketCommunicator, timeout=1.0):
    msg = await asyncio.wait_for(comm.receive_from(), timeout=timeout)
    return json.loads(msg)


@pytest.mark.asyncio
async def test_auth_required_for_anonymous(patch_redis):
    comm = WebsocketCommunicator(_app(), WS_PATH)
    # No user in scope -> anonymous
    connected, _ = await comm.connect()
    # The consumer accepts, sends auth_error, then closes with 4002
    msg = json.loads(await comm.receive_from())
    assert msg["type"] == "auth_error"
    assert msg["success"] is False
    # connection should close next
    close = await comm.wait_closed()
    assert close is True


@pytest.mark.asyncio
async def test_connect_presence_online_offline(user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    ONLINE_SET = mod.UserSocketConsumer.ONLINE_USERS_SET

    comm = await _connect_as(user)
    # at first tab for this user, they should be marked online
    assert user.id in patch_redis.sets.get(ONLINE_SET, set())

    await comm.disconnect()
    # last tab closed -> offline
    assert user.id not in patch_redis.sets.get(ONLINE_SET, set())


@pytest.mark.asyncio
async def test_chat_message_success(user, other_user, patch_redis):
    comm = await _connect_as(user)

    await comm.send_to(
        text_data=json.dumps(
            {
                "type": "chat_message",
                "message": "hello",
                "receiver_id": other_user.id,
            }
        )
    )

    # The sender also gets the event via its own group
    event = await _recv_json(comm)
    assert event["type"] == "chat_message"
    assert event["success"] is True
    assert event["data"]["content"] == "hello"
    assert event["data"]["sender_id"] == user.id
    assert event["data"]["receiver_id"] == other_user.id

    await comm.disconnect()


@pytest.mark.asyncio
async def test_chat_message_validation_error(user, patch_redis):
    comm = await _connect_as(user)

    await comm.send_to(text_data=json.dumps({"type": "chat_message", "message": "no receiver id"}))
    event = await _recv_json(comm)
    assert event["type"] == "error"
    assert event["success"] is False

    await comm.disconnect()


@pytest.mark.asyncio
async def test_message_edit_and_delete(user, other_user, patch_redis):
    # Create an initial message from 'user' to 'other_user' to edit/delete later.
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage

    # Connect both sides so group sends can deliver
    comm_sender = await _connect_as(user)
    comm_receiver = await _connect_as(other_user, path="/ws/user/?token=T2&tab_id=tab-2")

    m = ChatMessage.objects.create(
        sender_id=user.id,
        receiver_id=other_user.id,
        content="old",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )

    # Edit
    await comm_sender.send_to(
        text_data=json.dumps(
            {"type": "message_edit", "message_id": m.id, "new_content": "new content"}
        )
    )

    # Sender gets the edit event (and receiver, too)
    e1 = await _recv_json(comm_sender)
    assert e1["type"] == "message_edit"
    assert e1["data"]["message_id"] == m.id
    assert e1["data"]["new_content"] == "new content"

    e1r = await _recv_json(comm_receiver)
    assert e1r["type"] == "message_edit"

    m.refresh_from_db()
    assert m.content == "new content"

    # Delete
    await comm_sender.send_to(text_data=json.dumps({"type": "message_delete", "message_id": m.id}))
    d1 = await _recv_json(comm_sender)
    assert d1["type"] == "message_delete"
    d1r = await _recv_json(comm_receiver)
    assert d1r["type"] == "message_delete"

    m.refresh_from_db()
    assert m.is_deleted is True

    await comm_sender.disconnect()
    await comm_receiver.disconnect()


@pytest.mark.asyncio
async def test_message_edit_guard_unauthorized(other_user, user, patch_redis):
    # Message authored by 'other_user', but 'user' attempts to edit
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage

    m = ChatMessage.objects.create(
        sender_id=other_user.id,
        receiver_id=user.id,
        content="other authored",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )

    comm = await _connect_as(user)
    await comm.send_to(
        text_data=json.dumps({"type": "message_edit", "message_id": m.id, "new_content": "x"})
    )
    resp = await _recv_json(comm)
    assert resp["type"] == "error"
    assert "Message not found or unauthorized" in resp["message"]

    await comm.disconnect()


@pytest.mark.asyncio
async def test_chat_open_marks_seen_sets_key_and_ack(user, other_user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    ChatMessage = mod.ChatMessage
    consumer = mod.UserSocketConsumer

    # Seed: other_user -> user messages (status sent) should be marked as seen on open
    m1 = ChatMessage.objects.create(
        sender_id=other_user.id,
        receiver_id=user.id,
        content="hi",
        message_type="text",
        status="sent",
        created_at=timezone.now(),
    )
    ChatMessage.objects.create(
        sender_id=other_user.id,
        receiver_id=user.id,
        content="later",
        message_type="text",
        status="delivered",
        created_at=timezone.now(),
    )

    # Both sides connected (so group sends for batch status can deliver)
    comm_user = await _connect_as(user, path="/ws/user/?token=T3&tab_id=tA")
    comm_other = await _connect_as(other_user, path="/ws/user/?token=T4&tab_id=tB")

    await comm_user.send_to(
        text_data=json.dumps({"type": "chat_open", "receiver_id": other_user.id})
    )

    # other_user should receive a batch seen update
    batch = await _recv_json(comm_other)
    assert batch["type"] == "message_status_batch"
    seen_ids = {d["message_id"] for d in batch["data"]}
    assert m1.id in seen_ids

    # opener gets an ack
    ack = await _recv_json(comm_user)
    assert ack["type"] == "chat_open_ack"

    # Redis key set with expiry
    key = consumer._active_thread_key(consumer, user.id, "T3", "tA")  # using instance method signature
    assert key in patch_redis.kv
    assert patch_redis.kv[key] == str(other_user.id)
    assert patch_redis.expiries.get(key) == 30

    # DB statuses updated
    m1.refresh_from_db()
    assert m1.status == "seen"

    await comm_user.disconnect()
    await comm_other.disconnect()


@pytest.mark.asyncio
async def test_chat_close_clears_key_and_ack(user, other_user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    consumer = mod.UserSocketConsumer

    comm = await _connect_as(user, path="/ws/user/?token=T5&tab_id=tC")
    # Simulate chat_open having set the key
    key = consumer._active_thread_key(consumer, user.id, "T5", "tC")
    await patch_redis.set(key, str(other_user.id), ex=30)

    await comm.send_to(
        text_data=json.dumps({"type": "chat_close", "receiver_id": other_user.id})
    )
    ack = await _recv_json(comm)
    assert ack["type"] == "chat_close_ack"
    # key cleared
    assert key not in patch_redis.kv

    await comm.disconnect()


@pytest.mark.asyncio
async def test_heartbeat_extends_expiry(user, patch_redis):
    mod = importlib.import_module(CONSUMER_MODULE)
    consumer = mod.UserSocketConsumer

    comm = await _connect_as(user, path="/ws/user/?token=T6&tab_id=tD")
    key = consumer._active_thread_key(consumer, user.id, "T6", "tD")
    await patch_redis.set(key, "123", ex=5)

    await comm.send_to(text_data=json.dumps({"type": "heartbeat"}))
    # No event expected back; assert expiry extended
    await asyncio.sleep(0.05)
    assert patch_redis.expiries.get(key) == 30

    await comm.disconnect()


@pytest.mark.asyncio
async def test_typing_event_goes_to_receiver(user, other_user, patch_redis):
    # Two peers; 'user' types to 'other_user'
    comm_sender = await _connect_as(user, path="/ws/user/?token=T7&tab_id=tE")
    comm_receiver = await _connect_as(other_user, path="/ws/user/?token=T8&tab_id=tF")

    await comm_sender.send_to(
        text_data=json.dumps({"type": "chat_typing", "receiver_id": other_user.id})
    )

    evt = await _recv_json(comm_receiver)
    assert evt["type"] == "chat_typing"
    assert evt["success"] is True
    assert evt["data"]["sender_id"] == user.id
    assert evt["data"]["receiver_id"] == other_user.id

    await comm_sender.disconnect()
    await comm_receiver.disconnect()
