import math
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Q, Case, When, F

from asgiref.sync import async_to_sync

from utils.response import success_response, error_response
from utils.aws import s3, AWS_BUCKET, new_object_key
from .models import ChatMessage, MediaAsset
from .serializers import (
    CompleteUploadIn,
    ChatListSerializer,
    ChatMessageSerializer,
    SendMessageInSerializer,
    SignBatchInSerializer,
    ForwardMessageInSerializer
)
from background_worker.chats.tasks import (
    notify_message_event,
    process_video_task,
    process_image_task,
    process_audio_task,
    process_file_task
)
from django.contrib.auth import get_user_model
from .models import Conversation, ChatMessage
from .pagination import ChatListCursorPagination, MessageCursorPagination
from .services import ChatService

from utils.redis_client import ChatRedisService

User = get_user_model()

# class PrepareUpload(APIView):
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         ser = PrepareUploadIn(data=request.data)
#         if not ser.is_valid():
#             return error_response(message="Invalid data", errors=ser.errors, status=400)
        
#         d = ser.validated_data

#         # ------------------------------------------------------------------
#         # PATH 1: NEXT-BATCH (Renewing URLs for a specific large file)
#         # ------------------------------------------------------------------
#         if d.get("upload_id"):
#             return self._sign_batch(d)

#         # ------------------------------------------------------------------
#         # PATH 2: NEW MESSAGE (Album Creation)
#         # ------------------------------------------------------------------
#         receiver_id = d["receiver_id"]
#         text_caption = d.get("text", "")
#         attachments_data = d["attachments"]
        
#         msg_type = self._determine_message_type(attachments_data)
        
#         response_data = [] # List of upload instructions for client
#         assets_for_notify = [] # List of asset metadata for WebSocket

#         with transaction.atomic():
            
#             msg = ChatMessage.objects.create(
#                 sender=request.user,
#                 receiver_id=receiver_id,
#                 message_type=msg_type, # Or "album" if your model supports it
#                 content=text_caption,      # Save the caption!
#                 status="pending",
#             )

#             # 2. Loop & Create Assets
#             for item in attachments_data:
#                 file_name = item["file_name"]
#                 file_size = int(item["file_size"])
#                 content_type = item["content_type"]
#                 kind = item["kind"]

#                 # Generate Unique Key
#                 object_key = new_object_key(request.user.id, file_name)

#                 # DB Entry
#                 asset = MediaAsset.objects.create(
#                     message=msg,
#                     bucket=AWS_BUCKET,
#                     object_key=object_key,
#                     kind=kind,
#                     content_type=content_type,
#                     file_name=file_name,
#                     file_size=file_size,
#                     processing_status="queued",
#                 )

#                 # Prepare S3 Params
#                 upload_instructions = self._prepare_s3_params(
#                     asset, item, object_key, content_type
#                 )
                
#                 # Add asset_id so client knows which instruction belongs to which file
#                 upload_instructions["asset_id"] = asset.id
#                 response_data.append(upload_instructions)

#                 # Add to notification list
#                 assets_for_notify.append({
#                     "asset_id": asset.id,
#                     "kind": kind,
#                     "file_name": file_name,
#                     "processing_status": "queued",
#                     "thumbnail_url": None # Placeholder
#                 })

#         # 3. Notify Receiver: "Incoming Album..."
#         # We send the structure immediately so the receiver sees a gray grid
#         payload = {
#             "type": "chat_message",
#             "success": True,
#             "message": "Media upload started",
#             "data": {
#                 "message_id": msg.id,
#                 "message_type": msg.message_type,
#                 "text": msg.content,
#                 "status": "pending",
#                 "sender_id": msg.sender_id,
#                 "receiver_id": msg.receiver_id,
                
#                 # THE GRID DATA
#                 "stage": "uploading",
#                 "assets": assets_for_notify, # List of {asset_id, kind...}
#             },
#         }
#         notify_message_event.delay(payload)

#         # 4. Return Instructions to Sender
#         return success_response(
#             data={
#                 "message_id": msg.id,
#                 "uploads": response_data # List of S3 instructions
#             }, 
#             status=201
#         )
        
#     def _determine_message_type(self, attachments):
#         """
#         Classifies the message based on attachment count and types.
#         """
#         count = len(attachments)

#         # Case 1: Text Only (Should ideally use a separate endpoint, but handled here just in case)
#         if count == 0:
#             return ChatMessage.MsgType.TEXT

#         # Case 2: Single Attachment -> Inherit the specific type
#         if count == 1:
#             kind = attachments[0]['kind']
#             # Map frontend 'kind' to Model Choices
#             if kind == 'image': return ChatMessage.MsgType.IMAGE
#             if kind == 'video': return ChatMessage.MsgType.VIDEO
#             if kind == 'audio': return ChatMessage.MsgType.AUDIO
#             return ChatMessage.MsgType.FILE

#         # Case 3: Multiple Attachments -> Always ALBUM
#         # Whether it's 5 images, or 1 Image + 1 Video, it's a "Mixed/Grouped" message.
#         return ChatMessage.MsgType.ALBUM

#     def _prepare_s3_params(self, asset, item_data, object_key, content_type):
#         """Helper to generate S3 params for a single file"""
#         file_size = asset.file_size
        
#         # Direct Upload
#         if file_size <= DIRECT_THRESHOLD:
#             put_url = s3.generate_presigned_url(
#                 ClientMethod="put_object",
#                 Params={"Bucket": AWS_BUCKET, "Key": object_key, "ContentType": content_type},
#                 ExpiresIn=DEFAULT_EXPIRES_DIRECT,
#             )
#             return {
#                 "mode": "direct",
#                 "object_key": object_key,
#                 "put_url": put_url,
#             }

#         # Multipart Upload
#         else:
#             cps = int(item_data["client_part_size"])
#             cnp = int(item_data["client_num_parts"])
            
#             create = s3.create_multipart_upload(
#                 Bucket=AWS_BUCKET,
#                 Key=object_key,
#                 ContentType=content_type,
#                 ServerSideEncryption="AES256",
#             )
#             upload_id = create["UploadId"]
            
#             # Initial batch of URLs (usually covers the whole file for <5GB)
#             batch_count = min(item_data.get("batch_count") or 100, MAX_BATCH_COUNT)
#             items = []
#             max_pn = min(cnp, batch_count)
            
#             for pn in range(1, max_pn + 1):
#                 url = s3.generate_presigned_url(
#                     ClientMethod="upload_part",
#                     Params={"Bucket": AWS_BUCKET, "Key": object_key, "UploadId": upload_id, "PartNumber": pn},
#                     ExpiresIn=DEFAULT_EXPIRES_PART,
#                 )
#                 items.append({"part_number": pn, "url": url})

#             return {
#                 "mode": "multipart",
#                 "object_key": object_key,
#                 "upload_id": upload_id,
#                 "part_size": cps,
#                 "num_parts": cnp,
#                 "batch": {
#                     "items": items,
#                 }
#             }

#     def _sign_batch(self, d):
#         # ... (Keep existing logic for refreshing tokens) ...
#         # (This remains unchanged from your previous code)
#         start = int(d.get("start_part") or 1)
#         count = min(int(d.get("batch_count") or 100), MAX_BATCH_COUNT)
#         items = []
#         for pn in range(start, start + count):
#             url = s3.generate_presigned_url(
#                 ClientMethod="upload_part",
#                 Params={"Bucket": AWS_BUCKET, "Key": d["object_key"], "UploadId": d["upload_id"], "PartNumber": pn},
#                 ExpiresIn=DEFAULT_EXPIRES_PART,
#             )
#             items.append({"part_number": pn, "url": url})
            
#         return success_response(
#             data={
#                 "mode": "multipart",
#                 "object_key": d["object_key"],
#                 "upload_id": d["upload_id"],
#                 "batch": {
#                     "items": items
#                 }
#             }
#         )

# class CompleteUpload(APIView):
#     # ... (Your existing logic is MOSTLY fine, just one update below) ...
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         ser = CompleteUploadIn(data=request.data)
#         if not ser.is_valid():
#             return error_response(message="Invalid data", errors=ser.errors, status=400)
            
#         d = ser.validated_data
#         object_key = d["object_key"]

#         # ... (Logic to find asset and complete S3 multipart remains the same) ...
#         # ... (Assume S3 completion logic here is unchanged) ...
        
#         # 1. Fetch Asset
#         try:
#             asset = MediaAsset.objects.select_related("message").get(
#                 object_key=object_key,
#                 processing_status="queued"
#             )
#         except MediaAsset.DoesNotExist:
#             return error_response(message="Asset not found", status=404)
            
#         # 2. Complete S3 (Insert S3 Logic Here from your snippet) ...
#         if d.get("parts"):
#              s3.complete_multipart_upload(Bucket=asset.bucket, Key=object_key, UploadId=d["upload_id"], MultipartUpload={"Parts": d["parts"]})

#         # 3. Notify Receiver - UPDATED FOR ALBUMS
#         msg = asset.message
        
#         payload = {
#             "type": "chat_message_update", # Use UPDATE, not new message
#             "success": True,
#             "data": {
#                 "message_id": msg.id,
#                 "asset_id": asset.id,  # <--- CRITICAL: Tells UI which grid item to update
#                 "stage": "processing", # Change UI from "Uploading" to "Processing"
#                 "processing_status": "queued",
#                 "receiver_id": msg.receiver_id,
#             },
#         }
#         notify_message_event.delay(payload)

#         # 4. Trigger Worker
#         if asset.kind == MediaAsset.Kind.VIDEO:
#             process_video_task.delay(asset.id)
#         elif asset.kind == MediaAsset.Kind.IMAGE:
#             process_image_task.delay(asset.id)
#         elif asset.kind == MediaAsset.Kind.AUDIO:
#             process_audio_task.delay(asset.id)
#         elif asset.kind == MediaAsset.kind.FILE:
#             process_file_task.delay(asset.id)

#         return success_response(message="Upload completed", status=200)
    
    
    
    

class SendMessageView(APIView):
    """
    Unified Endpoint for Sending Messages.
    Optimized for High Throughput (Low Latency).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 1. VALIDATION LAYER
        # We use the serializer validation, which is robust.
        ser = SendMessageInSerializer(data=request.data)
        if not ser.is_valid():
            return error_response(message="Invalid data", errors=ser.errors, status=400)
        
        data = ser.validated_data
        user = request.user
        receiver_id = data['receiver_id']
        text = data.get('text', '') # Defaults to empty string
        reply_to_id = data.get('reply_to_id')
        attachments = data.get('attachments', [])

        # ------------------------------------
        # SCENARIO 1: MEDIA MESSAGE (Init S3)
        # ------------------------------------
        if attachments:
            # Atomic Transaction is handled inside Service
            msg, upload_instructions = ChatService.initialize_media_message(
                sender=user,
                receiver_id=receiver_id,
                text_caption=text,
                attachments=attachments,
                reply_to_id=reply_to_id
            )
            
            # OPTIMIZATION: Return strict JSON structure.
            # No heavy serialization here.
            return success_response(
                message="Media initialized",
                data={
                    "message_id": msg.id,
                    "type": "media",
                    "status": msg.status, # e.g. 'seen' or 'sent' based on Redis
                    "created_at": msg.created_at,
                    "uploads": upload_instructions # S3 URLs
                },
                status=201
            )

        # ------------------------------------
        # SCENARIO 2: TEXT MESSAGE
        # ------------------------------------
        else:
            # Atomic Transaction is handled inside Service
            msg = ChatService.send_text_message(
                sender=user,
                receiver_id=receiver_id,
                content=text,
                reply_to_id=reply_to_id
            )

            # OPTIMIZATION: "Lean Response"
            # We DO NOT serialize the full message object here.
            # The client already has the text; it just needs the ID and Timestamp to "confirm" the bubble.
            return success_response(
                message="Message sent",
                data={
                    "message_id": msg.id,
                    "type": "text",
                    "status": msg.status, # Frontend updates gray tick -> double tick immediately
                    "created_at": msg.created_at,
                    # "reply_metadata": ... (Only add if your frontend STRICTLY needs it to render the confirm)
                },
                status=201
            )
            
            
            
# --- COMPLETE UPLOAD VIEW (Kept largely the same but cleaned) ---
class CompleteUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(message="Invalid data", errors=ser.errors, status=400)
            
        d = ser.validated_data
        object_key = d["object_key"]

        # 1. Fetch Asset
        try:
            asset = MediaAsset.objects.select_related("message").get(
                object_key=object_key,
                processing_status="queued"
            )
        except MediaAsset.DoesNotExist:
            return error_response(message="Asset not found", status=404)
            
        # 2. Complete S3 Multipart (If applicable)
        if d.get("parts") and d.get("upload_id"):
             s3.complete_multipart_upload(
                 Bucket=asset.bucket, 
                 Key=object_key, 
                 UploadId=d["upload_id"], 
                 MultipartUpload={"Parts": d["parts"]}
             )

        if asset.kind == MediaAsset.Kind.VIDEO:
            process_video_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.IMAGE:
            process_image_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.AUDIO:
            process_audio_task.delay(asset.id)
        elif asset.kind == MediaAsset.kind.FILE:
            process_file_task.delay(asset.id)

        return success_response(message="Upload completed", status=200)
    

class SignBatchView(APIView):
    """
    Refreshes or generates the next batch of S3 Presigned URLs 
    for an ongoing Multipart Upload.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = SignBatchInSerializer(data=request.data)
        if not ser.is_valid():
            return error_response(errors=ser.errors, status=400)
        
        d = ser.validated_data
        upload_id = d["upload_id"]
        object_key = d["object_key"]
        start = d["start_part"]
        count = d["batch_count"]

        # Generate URLs for the requested range
        items = []
        for pn in range(start, start + count):
            url = s3.generate_presigned_url(
                ClientMethod="upload_part",
                Params={
                    "Bucket": AWS_BUCKET, 
                    "Key": object_key, 
                    "UploadId": upload_id, 
                    "PartNumber": pn
                },
                ExpiresIn=DEFAULT_EXPIRES_PART,
            )
            items.append({"part_number": pn, "url": url})
            
        return success_response(
            message="Batch signed",
            data={
                "object_key": object_key,
                "upload_id": upload_id,
                "batch": {
                    "items": items
                }
            },
            status=200
        )


class ForwardMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ForwardMessageInSerializer(data=request.data)
        if not ser.is_valid():
            return error_response(errors=ser.errors, status=400)
        
        d = ser.validated_data
        count = ChatService.forward_message_batch(
            sender=request.user,
            original_message_id=d['message_id'],
            receiver_ids=d['receiver_ids'],
            new_text=d.get('text')
        )

        return success_response(
            message=f"Forwarded to {count} chats",
            status=200
        )

class ChatListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = ChatListCursorPagination

    def get(self, request):
        user_id = request.user.id

        # --- 1. OPTIMIZED QUERY ---
        # We query the 'Conversation' container directly. 
        # The 'last_message' and 'unread_counts' are already stored here (Denormalized),
        # so we don't need any slow Subqueries or Joins on the Message table.
        queryset = Conversation.objects.filter(
            Q(participant_1=user_id) | Q(participant_2=user_id)
        ).annotate(
            # Calculate who the 'other person' is for every row
            partner_id=Case(
                When(participant_1=user_id, then=F('participant_2')),
                default=F('participant_1')
            )
        ).order_by('-updated_at')

        # --- 2. PAGINATION ---
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        if page is not None:
            # Extract IDs of the 20 users currently visible on screen
            partner_ids = [getattr(obj, 'partner_id') for obj in page]

            # --- 3. BATCH FETCH USERS (Solves N+1 DB) ---
            # Fetch all 20 partner objects in exactly 1 Database Query
            user_objects = User.objects.filter(id__in=partner_ids)
            # Create a lookup map: { 101: UserObject, 102: UserObject }
            user_map = {u.id: u for u in user_objects}

            # --- 4. REDIS PIPELINE (Solves N+1 Network) ---
            # Subscribe to updates AND get current online status in 1 Request
            online_status_map = {}
            if partner_ids:
                online_status_map = async_to_sync(ChatRedisService.subscribe_and_get_presences)(
                    observer_id=user_id,
                    target_ids=partner_ids
                )

            # --- 5. SERIALIZE ---
            # We pass the maps into the context so the Serializer doesn't have to query DB/Redis
            serializer = ChatListSerializer(
                page, 
                many=True, 
                context={
                    'request': request, # Needed for unread_count logic
                    'online_status_map': online_status_map,
                    'user_map': user_map
                }
            )
            
            return paginator.get_paginated_response(serializer.data)

        # Handle empty state
        return paginator.get_paginated_response([])
    
    







class ChatMessageListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = MessageCursorPagination

    def get(self, request, partner_id):
        user_id = request.user.id
        p1, p2 = sorted([user_id, partner_id])
        
        try:
            conversation = Conversation.objects.get(participant_1_id=p1, participant_2_id=p2)
        except Conversation.DoesNotExist:
            return self.pagination_class().get_paginated_response([])

        # --- 1. FETCH MESSAGES FIRST (Query 1) ---
        queryset = ChatMessage.objects.filter(
            conversation=conversation
        ).select_related('reply_to').prefetch_related('media_assets').order_by('-created_at')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        # --- 2. EXTRACT LATEST ID ---
        latest_msg_id = None
        
        # We only use the page data if we are on the FIRST page (No 'cursor' param).
        # If user is scrolling deep history (?cursor=...), the page[0] is NOT the latest message,
        # so we pass None and let the Service do the lookup query safely.
        is_first_page = request.query_params.get('cursor') is None
        
        if is_first_page and page:
            # The first item in the list is the absolute newest message
            # We also check if it was sent by the partner (not me)
            top_msg = page[0]
            if top_msg.sender_id == partner_id:
                latest_msg_id = top_msg.id

        # --- 3. MARK READ (Query 2 - Reusing ID) ---
        # If latest_msg_id is passed, the Service SKIPS the lookup query.
        ChatService.mark_messages_as_read(
            request.user, 
            conversation, 
            partner_id, 
            latest_message_id=latest_msg_id
        )
        
        serializer = ChatMessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)