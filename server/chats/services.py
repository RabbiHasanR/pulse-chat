from django.db import transaction
from django.db.models import F
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


from .models import Conversation, ChatMessage, MediaAsset
from .serializers import ChatMessageSerializer, ChatMessagePendingSerializer
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
    def _broadcast_message(sender_id, receiver_id, message_instance, serializer_class=None):
        """
        Helper: Sends the WebSocket event to BOTH Receiver and Sender (for Multi-Device Sync).
        """
        channel_layer = get_channel_layer()
        Serializer = serializer_class or ChatMessageSerializer
        serialized_data = Serializer(message_instance).data
        
        event = {
            "type": "forward_event", 
            "payload": {
                "type": "chat_message_new",
                "data": serialized_data
            }
        }
        
        # 1. Notify Receiver (So they see the new message)
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(receiver_id), 
            event
        )

        # 2. Notify Sender (So their OTHER devices update instantly)
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(sender_id), 
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


    @staticmethod
    @transaction.atomic
    def send_text_message(sender, receiver_id, content, reply_to_id=None):
        p1, p2 = sorted([sender.id, receiver_id])
        conversation, _ = Conversation.objects.select_for_update().get_or_create(
            participant_1_id=p1, participant_2_id=p2,
            defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
        )

        status, is_viewing = ChatService._determine_initial_status(sender.id, receiver_id)

        reply_to, reply_metadata = ChatService._get_reply_data(reply_to_id)

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

        ChatService._update_conversation(conversation, receiver_id, content, 'text', msg.created_at, is_viewing)
        ChatService._broadcast_message(sender.id, receiver_id, msg)
        
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

        # F. Update Conv & Broadcast
        # Note: We notify immediately so receiver sees "Sending photo..." (or the gray grid)
        ChatService._update_conversation(conversation, receiver_id, text_caption, msg_type, msg.created_at, is_viewing)
        ChatService._broadcast_message(sender.id, receiver_id, msg, serializer_class=ChatMessagePendingSerializer)

        return msg, upload_instructions

    @staticmethod
    def _generate_s3_params(asset, item_data, object_key):
        """Helper to generate S3 params for a single file"""
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
    def forward_message_batch(sender, original_message_id, receiver_ids, new_text=None):
        """
        Forwards a message to multiple users efficiently.
        """
        # 1. Fetch Original Message (Efficiently with assets and sender)
        try:
            original_msg = ChatMessage.objects.select_related('sender').prefetch_related('media_assets').get(id=original_message_id)
        except ChatMessage.DoesNotExist:
            return 0

        # 2. Determine Forward Metadata
        source_name = original_msg.forward_source_name or original_msg.sender.full_name
        
        # 3. Determine Content (Caption Logic)
        final_content = new_text if new_text is not None else original_msg.content
        msg_type = original_msg.message_type
        
        # 4. Process Each Receiver
        count = 0
        with transaction.atomic():
            for receiver_id in receiver_ids:
                if receiver_id == sender.id: 
                    continue # Skip self-forwarding

                # A. Setup Conversation
                p1, p2 = sorted([sender.id, receiver_id])
                conversation, _ = Conversation.objects.select_for_update().get_or_create(
                    participant_1_id=p1, participant_2_id=p2,
                    defaults={'unread_counts': {str(p1): 0, str(p2): 0}}
                )

                # B. Check Status (Reuse the Redis Logic!)
                status, is_viewing = ChatService._determine_initial_status(sender.id, receiver_id)

                # C. Create The New Message
                new_msg = ChatMessage.objects.create(
                    conversation=conversation,
                    sender=sender,
                    receiver_id=receiver_id,
                    content=final_content,
                    message_type=msg_type,
                    status=status,
                    # Forwarding Metadata
                    is_forwarded=True,
                    forward_source_name=source_name,
                    # Optimization: Copy the asset count
                    asset_count=original_msg.asset_count
                )

                # D. Clone Assets (The "Smart Copy")
                if original_msg.asset_count > 0:
                    new_assets = []
                    for old_asset in original_msg.media_assets.all():
                        new_assets.append(MediaAsset(
                            message=new_msg,
                            bucket=old_asset.bucket,
                            object_key=old_asset.object_key, # <--- REUSE KEY
                            kind=old_asset.kind,
                            content_type=old_asset.content_type,
                            file_name=old_asset.file_name,
                            file_size=old_asset.file_size,
                            width=old_asset.width,
                            height=old_asset.height,
                            duration_seconds=old_asset.duration_seconds,
                            variants=old_asset.variants, # Reuse thumbnails/variants
                            processing_status="done" # It is already processed!
                        ))
                    
                    if new_assets:
                        MediaAsset.objects.bulk_create(new_assets)

                # E. Update Conversation & Broadcast
                ChatService._update_conversation(
                    conversation, receiver_id, final_content, msg_type, new_msg.created_at, is_viewing
                )
                ChatService._broadcast_message(sender.id, receiver_id, new_msg)
                
                count += 1

        return count
    
    
    @staticmethod
    @transaction.atomic
    def mark_messages_as_read(reader, conversation, partner_id, latest_message_id=None):
        """
        Marks messages as read and sends Read Receipt (Blue Tick) to the Sender (Partner).
        """
        reader_str = str(reader.id)
        counts = conversation.unread_counts or {}

        # 1. OPTIMIZATION: If Badge is 0, DO NOTHING.
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
        ChatMessage.objects.filter(
            conversation=conversation,
            sender_id=partner_id,
            id__lte=cursor_id,
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
        
        # Notify the PARTNER (Sender) so their ticks turn blue
        async_to_sync(channel_layer.group_send)(
            ChatService._get_channel_group(partner_id), 
            event
        )