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
    PrepareUploadIn, 
    DIRECT_THRESHOLD, 
    MAX_BATCH_COUNT,
    CompleteUploadIn,
    DEFAULT_EXPIRES_DIRECT,
    DEFAULT_EXPIRES_PART,
    ChatListSerializer
)
from background_worker.chats.tasks import (
    notify_message_event,
    process_video_task,
    process_image_task,
    process_audio_task,
    process_file_task
)
from django.contrib.auth import get_user_model
from .models import Conversation
from .pagination import ChatListCursorPagination

from utils.redis_client import ChatRedisService

User = get_user_model()

class PrepareUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PrepareUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(message="Invalid data", errors=ser.errors, status=400)
        
        d = ser.validated_data

        # ------------------------------------------------------------------
        # PATH 1: NEXT-BATCH (Renewing URLs for a specific large file)
        # ------------------------------------------------------------------
        if d.get("upload_id"):
            return self._sign_batch(d)

        # ------------------------------------------------------------------
        # PATH 2: NEW MESSAGE (Album Creation)
        # ------------------------------------------------------------------
        receiver_id = d["receiver_id"]
        text_caption = d.get("text", "")
        attachments_data = d["attachments"]
        
        msg_type = self._determine_message_type(attachments_data)
        
        response_data = [] # List of upload instructions for client
        assets_for_notify = [] # List of asset metadata for WebSocket

        with transaction.atomic():
            
            msg = ChatMessage.objects.create(
                sender=request.user,
                receiver_id=receiver_id,
                message_type=msg_type, # Or "album" if your model supports it
                content=text_caption,      # Save the caption!
                status="pending",
            )

            # 2. Loop & Create Assets
            for item in attachments_data:
                file_name = item["file_name"]
                file_size = int(item["file_size"])
                content_type = item["content_type"]
                kind = item["kind"]

                # Generate Unique Key
                object_key = new_object_key(request.user.id, file_name)

                # DB Entry
                asset = MediaAsset.objects.create(
                    message=msg,
                    bucket=AWS_BUCKET,
                    object_key=object_key,
                    kind=kind,
                    content_type=content_type,
                    file_name=file_name,
                    file_size=file_size,
                    processing_status="queued",
                )

                # Prepare S3 Params
                upload_instructions = self._prepare_s3_params(
                    asset, item, object_key, content_type
                )
                
                # Add asset_id so client knows which instruction belongs to which file
                upload_instructions["asset_id"] = asset.id
                response_data.append(upload_instructions)

                # Add to notification list
                assets_for_notify.append({
                    "asset_id": asset.id,
                    "kind": kind,
                    "file_name": file_name,
                    "processing_status": "queued",
                    "thumbnail_url": None # Placeholder
                })

        # 3. Notify Receiver: "Incoming Album..."
        # We send the structure immediately so the receiver sees a gray grid
        payload = {
            "type": "chat_message",
            "success": True,
            "message": "Media upload started",
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "text": msg.content,
                "status": "pending",
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                
                # THE GRID DATA
                "stage": "uploading",
                "assets": assets_for_notify, # List of {asset_id, kind...}
            },
        }
        notify_message_event.delay(payload)

        # 4. Return Instructions to Sender
        return success_response(
            data={
                "message_id": msg.id,
                "uploads": response_data # List of S3 instructions
            }, 
            status=201
        )
        
    def _determine_message_type(self, attachments):
        """
        Classifies the message based on attachment count and types.
        """
        count = len(attachments)

        # Case 1: Text Only (Should ideally use a separate endpoint, but handled here just in case)
        if count == 0:
            return ChatMessage.MsgType.TEXT

        # Case 2: Single Attachment -> Inherit the specific type
        if count == 1:
            kind = attachments[0]['kind']
            # Map frontend 'kind' to Model Choices
            if kind == 'image': return ChatMessage.MsgType.IMAGE
            if kind == 'video': return ChatMessage.MsgType.VIDEO
            if kind == 'audio': return ChatMessage.MsgType.AUDIO
            return ChatMessage.MsgType.FILE

        # Case 3: Multiple Attachments -> Always ALBUM
        # Whether it's 5 images, or 1 Image + 1 Video, it's a "Mixed/Grouped" message.
        return ChatMessage.MsgType.ALBUM

    def _prepare_s3_params(self, asset, item_data, object_key, content_type):
        """Helper to generate S3 params for a single file"""
        file_size = asset.file_size
        
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
            
            # Initial batch of URLs (usually covers the whole file for <5GB)
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

    def _sign_batch(self, d):
        # ... (Keep existing logic for refreshing tokens) ...
        # (This remains unchanged from your previous code)
        start = int(d.get("start_part") or 1)
        count = min(int(d.get("batch_count") or 100), MAX_BATCH_COUNT)
        items = []
        for pn in range(start, start + count):
            url = s3.generate_presigned_url(
                ClientMethod="upload_part",
                Params={"Bucket": AWS_BUCKET, "Key": d["object_key"], "UploadId": d["upload_id"], "PartNumber": pn},
                ExpiresIn=DEFAULT_EXPIRES_PART,
            )
            items.append({"part_number": pn, "url": url})
            
        return success_response(
            data={
                "mode": "multipart",
                "object_key": d["object_key"],
                "upload_id": d["upload_id"],
                "batch": {
                    "items": items
                }
            }
        )

class CompleteUpload(APIView):
    # ... (Your existing logic is MOSTLY fine, just one update below) ...
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(message="Invalid data", errors=ser.errors, status=400)
            
        d = ser.validated_data
        object_key = d["object_key"]

        # ... (Logic to find asset and complete S3 multipart remains the same) ...
        # ... (Assume S3 completion logic here is unchanged) ...
        
        # 1. Fetch Asset
        try:
            asset = MediaAsset.objects.select_related("message").get(
                object_key=object_key,
                processing_status="queued"
            )
        except MediaAsset.DoesNotExist:
            return error_response(message="Asset not found", status=404)
            
        # 2. Complete S3 (Insert S3 Logic Here from your snippet) ...
        if d.get("parts"):
             s3.complete_multipart_upload(Bucket=asset.bucket, Key=object_key, UploadId=d["upload_id"], MultipartUpload={"Parts": d["parts"]})

        # 3. Notify Receiver - UPDATED FOR ALBUMS
        msg = asset.message
        
        payload = {
            "type": "chat_message_update", # Use UPDATE, not new message
            "success": True,
            "data": {
                "message_id": msg.id,
                "asset_id": asset.id,  # <--- CRITICAL: Tells UI which grid item to update
                "stage": "processing", # Change UI from "Uploading" to "Processing"
                "processing_status": "queued",
                "receiver_id": msg.receiver_id,
            },
        }
        notify_message_event.delay(payload)

        # 4. Trigger Worker
        if asset.kind == MediaAsset.Kind.VIDEO:
            process_video_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.IMAGE:
            process_image_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.AUDIO:
            process_audio_task.delay(asset.id)
        # ... etc

        return success_response(message="Upload completed", status=200)
    
    
    
    
    
    
    

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