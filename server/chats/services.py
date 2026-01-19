from django.db import transaction
from django.db.models import F
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Conversation, ChatMessage
from .serializers import ChatMessageSerializer
from utils.redis_client import RedisKeys, redis_client


class ChatService:
    @staticmethod
    def _get_channel_group(user_id):
        return f"user_{user_id}"

    @staticmethod
    @transaction.atomic
    def send_message(sender, receiver_id, content, msg_type='text', reply_to_id=None):
        """
        1. Determines Status (Seen vs Delivered vs Sent) via Redis.
        2. Creates Message Row.
        3. Updates Conversation (Denormalization).
        4. Sends Real-Time Socket Event to Receiver.
        """
        # A. Get/Create Conversation (Ensure sorted IDs for uniqueness)
        p1, p2 = sorted([sender.id, receiver_id])
        conversation, created = Conversation.objects.select_for_update().get_or_create(
            participant_1_id=p1,
            participant_2_id=p2,
            defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
        )

        # B. Handle Reply Logic
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

        # --- C. DETERMINE INITIAL STATUS (The Logic You Requested) ---
        
        # 1. Check Redis: Is the receiver actively looking at THIS chat?
        # Key: "user:{receiver_id}:viewing:{sender_id}"
        viewing_key = RedisKeys.viewing(receiver_id, sender.id)
        # We use async_to_sync because we are inside a sync DB transaction
        is_viewing = async_to_sync(redis_client.scard)(viewing_key) > 0

        # 2. Check Redis: Is the receiver Online at all?
        # Key: "online_users" set
        is_online = False
        if not is_viewing:
            is_online = async_to_sync(redis_client.sismember)(RedisKeys.ONLINE_USERS, receiver_id)

        # 3. Set Status
        if is_viewing:
            initial_status = ChatMessage.Status.SEEN
        elif is_online:
            initial_status = ChatMessage.Status.DELIVERED
        else:
            initial_status = ChatMessage.Status.SENT

        # --- D. CREATE MESSAGE ---
        msg = ChatMessage.objects.create(
            conversation=conversation,
            sender=sender,
            receiver_id=receiver_id,
            content=content,
            message_type=msg_type,
            reply_to=reply_to,
            reply_metadata=reply_metadata,
            status=initial_status # <--- Applied here
        )

        # --- E. UPDATE CONVERSATION (Denormalization) ---
        current_counts = conversation.unread_counts or {}
        receiver_str = str(receiver_id)

        # CRITICAL: Only increment unread count if they are NOT viewing
        if not is_viewing:
            current_counts[receiver_str] = current_counts.get(receiver_str, 0) + 1
        
        conversation.last_message_content = content
        conversation.last_message_type = msg_type
        conversation.last_message_time = msg.created_at
        conversation.unread_counts = current_counts
        conversation.save()

        # --- F. WEBSOCKET NOTIFICATION ---
        channel_layer = get_channel_layer()
        serialized_data = ChatMessageSerializer(msg).data
        
        event = {
            "type": "forward_event", 
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
    def mark_messages_as_read(reader, conversation, partner_id, latest_message_id=None):
        """
        OPTIMIZED:
        - If 'latest_message_id' is passed, we SKIP the lookup query.
        - Otherwise, we find it ourselves (fallback for scrolling/history).
        """
        reader_str = str(reader.id)
        counts = conversation.unread_counts or {}

        # 1. OPTIMIZATION: If Badge is 0, DO NOTHING.
        # This makes 99% of page refreshes cost 0 DB Writes.
        if counts.get(reader_str, 0) == 0:
            return

        # 2. Reset Badge
        counts[reader_str] = 0
        conversation.unread_counts = counts
        conversation.save(update_fields=['unread_counts'])

        # 3. Determine the 'Cursor' (Last Read ID)
        cursor_id = latest_message_id

        if not cursor_id:
            # Fallback: Query the DB if we weren't given the ID
            last_msg_obj = ChatMessage.objects.filter(
                conversation=conversation,
                sender_id=partner_id
            ).order_by('-created_at').first()
            
            if last_msg_obj:
                cursor_id = last_msg_obj.id

        if not cursor_id:
            return

        # 4. Bulk Update (The Range Update)
        # "Mark everything OLDER than or EQUAL to cursor as SEEN"
        ChatMessage.objects.filter(
            conversation=conversation,
            sender_id=partner_id,
            id__lte=cursor_id, # <--- Safety Check
            status__in=[ChatMessage.Status.SENT, ChatMessage.Status.DELIVERED]
        ).update(status=ChatMessage.Status.SEEN)

        # 5. WebSocket Notification (O(1) Payload)
        channel_layer = get_channel_layer()
        event = {
            "type": "forward_event",
            "payload": {
                "type": "chat_read_receipt",
                "data": {
                    "conversation_id": conversation.id,
                    "reader_id": reader.id,
                    "last_read_id": cursor_id
                }
            }
        }
        
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(partner_id), 
            event
        )