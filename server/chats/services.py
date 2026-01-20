from django.db import transaction
from django.db.models import F
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


from .models import Conversation, ChatMessage, MediaAsset
from .serializers import ChatMessageSerializer
from utils.redis_client import redis_client, RedisKeys
from utils.s3 import s3, new_object_key, AWS_BUCKET, DEFAULT_EXPIRES_DIRECT, DEFAULT_EXPIRES_PART, MAX_BATCH_COUNT, DIRECT_THRESHOLD

class ChatService:
    @staticmethod
    def _get_channel_group(user_id):
        return f"user_{user_id}"

    @staticmethod
    def _determine_initial_status(sender_id, receiver_id):
        """
        Helper: Checks Redis to see if the user is Viewing or Online.
        Returns: (status, is_viewing)
        """
        # 1. Check Viewing (Blue Ticks)
        viewing_key = RedisKeys.viewing(receiver_id, sender_id)
        # async_to_sync required because we are in a synchronous context
        is_viewing = async_to_sync(redis_client.scard)(viewing_key) > 0

        # 2. Check Online (Double Ticks)
        is_online = False
        if not is_viewing:
            is_online = async_to_sync(redis_client.sismember)(RedisKeys.ONLINE_USERS, receiver_id)

        # 3. Determine Status
        if is_viewing:
            return ChatMessage.Status.SEEN, True
        elif is_online:
            return ChatMessage.Status.DELIVERED, False
        else:
            return ChatMessage.Status.SENT, False

    @staticmethod
    def _update_conversation(conversation, receiver_id, content, msg_type, msg_time, is_viewing):
        """
        Helper: Updates the denormalized fields on the Conversation model.
        """
        current_counts = conversation.unread_counts or {}
        receiver_str = str(receiver_id)

        # Increment Unread Count (Only if NOT viewing)
        if not is_viewing:
            current_counts[receiver_str] = current_counts.get(receiver_str, 0) + 1
        
        # Set Last Message Preview
        preview_text = content
        if msg_type != ChatMessage.MsgType.TEXT:
            if not content: # If no caption
                if msg_type == ChatMessage.MsgType.IMAGE: preview_text = "📷 Image"
                elif msg_type == ChatMessage.MsgType.VIDEO: preview_text = "🎥 Video"
                elif msg_type == ChatMessage.MsgType.AUDIO: preview_text = "🎤 Audio"
                else: preview_text = "📁 File"
            else:
                 preview_text = f"📷 {content}" if msg_type == ChatMessage.MsgType.IMAGE else content

        conversation.last_message_content = preview_text
        conversation.last_message_type = msg_type
        conversation.last_message_time = msg_time
        conversation.unread_counts = current_counts
        conversation.save(update_fields=['last_message_content', 'last_message_type', 'last_message_time', 'unread_counts', 'updated_at'])

    @staticmethod
    def _notify_receiver(receiver_id, message_instance):
        """Helper: Sends the WebSocket event"""
        channel_layer = get_channel_layer()
        serialized_data = ChatMessageSerializer(message_instance).data
        
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

    @staticmethod
    def _get_reply_data(reply_to_id):
        if not reply_to_id: return None, None
        try:
            parent = ChatMessage.objects.get(id=reply_to_id)
            meta = {
                "id": parent.id,
                "sender_name": parent.sender.full_name,
                "preview": parent.content[:50] if parent.content else "Media",
                "msg_type": parent.message_type
            }
            return parent, meta
        except ChatMessage.DoesNotExist:
            return None, None

    @staticmethod
    def _determine_msg_type(attachments):
        if len(attachments) > 1: return ChatMessage.MsgType.ALBUM
        if len(attachments) == 0: return ChatMessage.MsgType.TEXT
        kind = attachments[0]['kind']
        if kind == 'image': return ChatMessage.MsgType.IMAGE
        if kind == 'video': return ChatMessage.MsgType.VIDEO
        if kind == 'audio': return ChatMessage.MsgType.AUDIO
        return ChatMessage.MsgType.FILE

    # =========================================================================
    # 1. SEND TEXT MESSAGE
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def send_text_message(sender, receiver_id, content, reply_to_id=None):
        # A. Setup Conversation
        p1, p2 = sorted([sender.id, receiver_id])
        conversation, _ = Conversation.objects.select_for_update().get_or_create(
            participant_1_id=p1, participant_2_id=p2,
            defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
        )

        # B. Determine Status
        status, is_viewing = ChatService._determine_initial_status(sender.id, receiver_id)

        # C. Handle Reply
        reply_to, reply_metadata = ChatService._get_reply_data(reply_to_id)

        # D. Create Message
        msg = ChatMessage.objects.create(
            conversation=conversation,
            sender=sender,
            receiver_id=receiver_id,
            content=content,
            message_type=ChatMessage.MsgType.TEXT,
            reply_to=reply_to,
            reply_metadata=reply_metadata,
            status=status
        )

        # E. Update Conv & Notify
        ChatService._update_conversation(conversation, receiver_id, content, 'text', msg.created_at, is_viewing)
        ChatService._notify_receiver(receiver_id, msg)
        
        return msg

    # =========================================================================
    # 2. INITIALIZE MEDIA MESSAGE (S3 Prep)
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def initialize_media_message(sender, receiver_id, text_caption, attachments, reply_to_id=None):
        """
        Creates the Message (Status based on Redis) and MediaAssets.
        Returns: The Message Object AND the S3 Upload Instructions.
        """
        # A. Setup Conversation
        p1, p2 = sorted([sender.id, receiver_id])
        conversation, _ = Conversation.objects.select_for_update().get_or_create(
            participant_1_id=p1, participant_2_id=p2,
            defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
        )

        # B. Determine Status
        status, is_viewing = ChatService._determine_initial_status(sender.id, receiver_id)

        # C. Determine Msg Type
        msg_type = ChatService._determine_msg_type(attachments)

        # D. Create Message
        reply_to, reply_metadata = ChatService._get_reply_data(reply_to_id)
        
        msg = ChatMessage.objects.create(
            conversation=conversation,
            sender=sender,
            receiver_id=receiver_id,
            content=text_caption, 
            message_type=msg_type,
            reply_to=reply_to,
            reply_metadata=reply_metadata,
            status=status, 
            asset_count=len(attachments)
        )

        # E. Process Attachments (Create Assets & Generate URLs)
        upload_instructions = []
        
        for item in attachments:
            file_name = item["file_name"]
            kind = item["kind"]
            object_key = new_object_key(sender.id, file_name)

            # Create Asset Row
            asset = MediaAsset.objects.create(
                message=msg,
                bucket=AWS_BUCKET,
                object_key=object_key,
                kind=kind,
                content_type=item["content_type"],
                file_name=file_name,
                file_size=item["file_size"],
                processing_status="queued"
            )

            # Generate S3 Params (Reuse your S3 logic here)
            params = ChatService._generate_s3_params(asset, item, object_key)
            params["asset_id"] = asset.id
            upload_instructions.append(params)

        # F. Update Conv & Notify
        # Note: We notify immediately so receiver sees "Sending photo..." (or the gray grid)
        ChatService._update_conversation(conversation, receiver_id, text_caption, msg_type, msg.created_at, is_viewing)
        ChatService._notify_receiver(receiver_id, msg)

        return msg, upload_instructions

    @staticmethod
    def _generate_s3_params(asset, item_data, object_key):
        """Helper to generate S3 params for a single file (Adapted from your logic)"""
        file_size = asset.file_size
        content_type = asset.content_type
        
        # Direct Upload
        if file_size <= DIRECT_THRESHOLD:
            put_url = s3.generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": AWS_BUCKET, "Key": object_key, "ContentType": content_type},
                ExpiresIn=DEFAULT_EXPIRES_DIRECT,
            )
            return {
                "mode": "direct",
                "object_key": object_key,
                "put_url": put_url,
            }
        # Multipart Upload
        else:
            cps = int(item_data["client_part_size"])
            cnp = int(item_data["client_num_parts"])
            
            create = s3.create_multipart_upload(
                Bucket=AWS_BUCKET,
                Key=object_key,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
            upload_id = create["UploadId"]
            
            # Initial batch of URLs
            batch_count = min(item_data.get("batch_count") or 100, MAX_BATCH_COUNT)
            items = []
            max_pn = min(cnp, batch_count)
            
            for pn in range(1, max_pn + 1):
                url = s3.generate_presigned_url(
                    ClientMethod="upload_part",
                    Params={"Bucket": AWS_BUCKET, "Key": object_key, "UploadId": upload_id, "PartNumber": pn},
                    ExpiresIn=DEFAULT_EXPIRES_PART,
                )
                items.append({"part_number": pn, "url": url})

            return {
                "mode": "multipart",
                "object_key": object_key,
                "upload_id": upload_id,
                "part_size": cps,
                "num_parts": cnp,
                "batch": {
                    "items": items,
                }
            }
    
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