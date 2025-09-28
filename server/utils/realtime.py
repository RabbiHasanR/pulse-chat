from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.utils import timezone
from chats.models import ChatMessage
from .redis_client import redis_client


def room(user_id: int) -> str:
    return f"user_{user_id}"


async def is_user_viewing_me(receiver_id: int, sender_id: int) -> bool:
    pattern = f"user:{receiver_id}:access:*:tab:*:active_thread"
    keys = await redis_client.keys(pattern)
    if not keys:
        return False
    values = await redis_client.mget(*keys)
    decoded = [v.decode() for v in values if v]
    return str(sender_id) in decoded


async def notify_single_status(message_id: int, receiver_id: int, sender_id: int, status: str) -> None:
    payload = {
        "type": "message_status",
        "success": True,
        "message": f"Message {status}",
        "data": {
            "message_id": message_id,
            "status": status,
            "receiver_id": receiver_id,
            "sender_id": sender_id,
            "timestamp": str(timezone.now()),
        },
    }
    channel_layer = get_channel_layer()
    await channel_layer.group_send(room(sender_id), {"type": "forward_event", "payload": payload})


async def send_unread_summary(to_user_id: int, from_user_id: int) -> None:
    unseen_count = await ChatMessage.objects.filter(
        sender_id=from_user_id, receiver_id=to_user_id, status="sent"
    ).acount()

    last_message = await ChatMessage.objects.filter(
        sender_id=from_user_id, receiver_id=to_user_id
    ).order_by("-created_at").afirst()

    summary_payload = {
        "type": "chat_summary",
        "success": True,
        "message": "Unread message summary",
        "data": {
            "sender_id": from_user_id,
            "receiver_id": to_user_id,
            "unread_count": unseen_count,
            "last_message": {
                "message_id": last_message.id if last_message else None,
                "content": last_message.content if last_message else None,
                "timestamp": str(last_message.created_at) if last_message else None,
            },
        },
    }
    channel_layer = get_channel_layer()
    await channel_layer.group_send(room(to_user_id), {"type": "forward_event", "payload": summary_payload})
