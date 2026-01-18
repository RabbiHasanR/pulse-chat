from django.db import transaction
from django.db.models import F
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Conversation, ChatMessage
from .serializers import ChatMessageSerializer

class ChatService:
    @staticmethod
    def _get_channel_group(user_id):
        return f"user_{user_id}"

    @staticmethod
    @transaction.atomic
    def send_message(sender, receiver_id, content, msg_type='text', reply_to_id=None):
        """
        1. Creates Message Row
        2. Updates Conversation (Denormalization)
        3. Sends Real-Time Socket Event to Receiver
        """
        # A. Get/Create Conversation (Ensure sorted IDs for uniqueness)
        p1, p2 = sorted([sender.id, receiver_id])
        conversation, created = Conversation.objects.select_for_update().get_or_create(
            participant_1_id=p1,
            participant_2_id=p2,
            defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
        )

        # B. Handle Reply Logic (Optional)
        reply_to = None
        reply_metadata = None
        if reply_to_id:
            try:
                reply_parent = ChatMessage.objects.get(id=reply_to_id)
                reply_to = reply_parent
                # Snapshot for UI
                reply_metadata = {
                    "id": reply_parent.id,
                    "sender_name": reply_parent.sender.full_name,
                    "preview": reply_parent.content[:50] if reply_parent.content else "Media",
                    "msg_type": reply_parent.message_type
                }
            except ChatMessage.DoesNotExist:
                pass

        # C. Create Message
        msg = ChatMessage.objects.create(
            conversation=conversation,
            sender=sender,
            receiver_id=receiver_id,
            content=content,
            message_type=msg_type,
            reply_to=reply_to,
            reply_metadata=reply_metadata
        )

        # D. Update Conversation Denormalization (Unread + Last Msg)
        current_counts = conversation.unread_counts or {}
        receiver_str = str(receiver_id)
        current_counts[receiver_str] = current_counts.get(receiver_str, 0) + 1

        conversation.last_message_content = content
        conversation.last_message_type = msg_type
        conversation.last_message_time = msg.created_at
        conversation.unread_counts = current_counts
        conversation.save()

        # E. WebSocket Notification: "New Message"
        # We define a custom 'type' that the frontend listener handles
        channel_layer = get_channel_layer()
        serialized_data = ChatMessageSerializer(msg).data
        
        event = {
            "type": "forward_event", # Funnels through Consumer's generic handler
            "payload": {
                "type": "chat_message_new",
                "data": serialized_data
            }
        }
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(receiver_id), 
            event
        )

        return msg

    @staticmethod
    @transaction.atomic
    def mark_messages_as_read(reader, conversation, partner_id):
        """
        1. Resets unread count in Conversation.
        2. Bulk updates Message status to 'seen'.
        3. Sends 'Read Receipt' (Blue Ticks) to the SENDER.
        """
        reader_str = str(reader.id)
        counts = conversation.unread_counts or {}

        # 1. OPTIMIZATION: Only proceed if there are actually unread items
        if counts.get(reader_str, 0) == 0:
            return

        # 2. Reset Badge Count
        counts[reader_str] = 0
        conversation.unread_counts = counts
        conversation.save(update_fields=['unread_counts'])

        # 3. Find specific messages to update
        # We need the IDs to tell the frontend "These specific bubbles turned blue"
        unread_msgs = ChatMessage.objects.filter(
            conversation=conversation,
            sender_id=partner_id,
            status__in=[ChatMessage.Status.SENT, ChatMessage.Status.DELIVERED]
        )
        
        # Grab IDs *before* updating (values_list is fast)
        read_message_ids = list(unread_msgs.values_list('id', flat=True))
        
        if not read_message_ids:
            return

        # 4. Bulk DB Update
        unread_msgs.update(status=ChatMessage.Status.SEEN)

        # 5. WebSocket Notification: "Read Receipt"
        # Notify the PARTNER (The one who sent the messages)
        channel_layer = get_channel_layer()
        event = {
            "type": "forward_event",
            "payload": {
                "type": "chat_read_receipt", # Frontend event listener name
                "data": {
                    "conversation_id": conversation.id,
                    "reader_id": reader.id,
                    "message_ids": read_message_ids # List of [101, 102, 103]
                }
            }
        }
        
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(partner_id), 
            event
        )