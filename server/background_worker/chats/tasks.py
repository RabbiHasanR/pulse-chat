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
    processing_status = data.get("processing_status", "queued")

    if not message_id or not sender_id or not receiver_id:
        return

    channel_layer = get_channel_layer()

    # ---------------------------------------------------------
    # 1. NOTIFICATIONS (Keep both for multi-device sync)
    # ---------------------------------------------------------
    
    # Notify Sender (Keeps laptop/phone in sync with the uploading device)
    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # Notify Receiver (So they see the "Incoming..." bubble)
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # ---------------------------------------------------------
    # 2. PRESENCE LOGIC (The "Seen" Fix)
    # ---------------------------------------------------------
    
    # Only mark as "SEEN" if the file is actually ready.
    # We skip this logic if the file is still uploading (pending) or processing.
    is_media_ready = (status != "pending") and (processing_status == "done")
    
    # Also apply this logic for normal text messages (which don't have processing_status)
    is_text_message = (data.get("message_type") == "text")
    
    should_check_seen = is_media_ready or is_text_message

    if should_check_seen:
        # Check if receiver is online
        if async_to_sync(redis_client.sismember)("online_users", receiver_id):
            # Check if receiver is currently looking at this chat
            if async_to_sync(is_user_viewing_me)(receiver_id=receiver_id, sender_id=sender_id):
                
                # 1. Update DB
                ChatMessage.objects.filter(id=message_id).update(status="seen")
                
                # 2. Notify Sender: "User saw your message"
                async_to_sync(notify_single_status)(
                    message_id=message_id,
                    receiver_id=receiver_id,
                    sender_id=sender_id,
                    status="seen",
                )
            else:
                # Online but in different chat -> Unread Count ++
                async_to_sync(send_unread_summary)(
                    to_user_id=receiver_id,
                    from_user_id=sender_id,
                )
        else:
            # Offline -> Unread Count ++
            async_to_sync(send_unread_summary)(
                to_user_id=receiver_id,
                from_user_id=sender_id,
            )