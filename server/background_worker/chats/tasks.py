# tasks.py
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from utils.redis_client import redis_client
from utils.realtime import (
    room,
    is_user_viewing_me,
    notify_single_status,
    send_unread_summary,
)
from chats.models import ChatMessage

@shared_task
def notify_message_event(payload: dict):
    data = payload.get("data", {})
    message_id = data.get("message_id")
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    status = data.get("status", "pending")

    if not message_id or not sender_id or not receiver_id:
        return

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # Presence-aware status update
    if async_to_sync(redis_client.sismember)("online_users", receiver_id):
        if async_to_sync(is_user_viewing_me)(receiver_id=receiver_id, sender_id=sender_id):
            ChatMessage.objects.filter(id=message_id).update(status="seen")
            async_to_sync(notify_single_status)(
                message_id=message_id,
                receiver_id=receiver_id,
                sender_id=sender_id,
                status="seen",
            )
        else:
            async_to_sync(send_unread_summary)(
                to_user_id=receiver_id,
                from_user_id=sender_id,
            )
