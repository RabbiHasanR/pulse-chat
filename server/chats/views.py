import math
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction

from utils.aws import s3, AWS_BUCKET, new_object_key
from .models import ChatMessage, MediaAsset
from .serializers import (
    PrepareUploadIn, 
    DIRECT_THRESHOLD, 
    MAX_BATCH_COUNT,
    CompleteUploadIn,
)
from background_worker.chats.tasks import notify_message_event, process_uploaded_asset

class PrepareUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PrepareUploadIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # NEXT-BATCH (Client asks for more parts)
        if d.get("upload_id"):
            return self._sign_batch(d)

        # FIRST CALL (Client starts upload)
        file_size = int(d["file_size"])
        file_name = d["file_name"]
        object_key = new_object_key(request.user.id, file_name)

        with transaction.atomic():
            # 1. Create Message (Status: Pending)
            msg = ChatMessage.objects.create(
                sender=request.user,
                receiver_id=d["receiver_id"],
                message_type=d["message_type"],
                file_name=file_name,
                file_size=file_size,
                status="pending",
            )

            # 2. Create Asset (Status: Queued)
            asset = MediaAsset.objects.create(
                message=msg,
                bucket=AWS_BUCKET,
                object_key=object_key,
                kind=d["message_type"],
                content_type=d["content_type"],
                file_name=file_name,
                file_size=file_size,
                processing_status="queued",
            )

        # 3. Notify Receiver: "Incoming File..."
        payload = {
            "type": "chat_message",
            "success": True,
            "message": "Media message started",
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "status": msg.status,                 # pending
                "processing_status": "queued",        
                
                # --- UI INSTRUCTION: SHOW "INCOMING..." ---
                "stage": "uploading",                 
                # ------------------------------------------

                "file_name": asset.file_name,
                "file_size": asset.file_size,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content_type": asset.content_type,
            },
        }
        notify_message_event.delay(payload)

        # 4. Generate S3 URLs (Direct or Multipart)
        if file_size <= DIRECT_THRESHOLD:
            # Direct Upload
            put_url = s3.generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": AWS_BUCKET, "Key": object_key, "ContentType": d["content_type"]},
                ExpiresIn=DEFAULT_EXPIRES_DIRECT,
            )
            return Response({
                "mode": "direct",
                "object_key": object_key,
                "put_url": put_url,
                "expires_in": DEFAULT_EXPIRES_DIRECT,
                "message_id": msg.id,
            }, status=201)

        else:
            # Multipart Upload
            cps = int(d["client_part_size"])
            cnp = int(d["client_num_parts"])
            
            create = s3.create_multipart_upload(
                Bucket=AWS_BUCKET,
                Key=object_key,
                ContentType=d["content_type"],
                ServerSideEncryption="AES256",
            )
            upload_id = create["UploadId"]

            batch_count = min(d.get("batch_count") or 100, MAX_BATCH_COUNT)
            items = []
            max_pn = min(cnp, batch_count)
            
            for pn in range(1, max_pn + 1):
                url = s3.generate_presigned_url(
                    ClientMethod="upload_part",
                    Params={"Bucket": AWS_BUCKET, "Key": object_key, "UploadId": upload_id, "PartNumber": pn},
                    ExpiresIn=DEFAULT_EXPIRES_PART,
                )
                items.append({"part_number": pn, "url": url})

            return Response({
                "mode": "multipart",
                "object_key": object_key,
                "upload_id": upload_id,
                "part_size": cps,
                "num_parts": cnp,
                "batch": {
                    "start_part": 1,
                    "count": max_pn,
                    "expires_in": DEFAULT_EXPIRES_PART,
                    "items": items,
                },
                "message_id": msg.id,
            }, status=201)

    def _sign_batch(self, d):
        """Helper for fetching more multipart URLs"""
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
        return Response({
            "mode": "multipart",
            "object_key": d["object_key"],
            "upload_id": d["upload_id"],
            "batch": {
                "start_part": start,
                "count": count,
                "expires_in": DEFAULT_EXPIRES_PART,
                "items": items
            }
        })


class CompleteUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteUploadIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        object_key = d["object_key"]
        upload_id = d["upload_id"]
        parts = d.get("parts", [])

        # 1. Find the asset (must be 'queued' to prevent double processing)
        try:
            asset = MediaAsset.objects.select_related("message").get(
                object_key=object_key,
                processing_status="queued"
            )
        except MediaAsset.DoesNotExist:
            return Response({"error": "Asset not found or already processed"}, status=404)

        # 2. Complete multipart upload on S3 (if applicable)
        if parts:
            try:
                s3.complete_multipart_upload(
                    Bucket=asset.bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception as e:
                return Response({"error": f"S3 Error: {str(e)}"}, status=400)

        # 3. Mark asset as ready (remains 'queued' until worker picks it up)
        # We update updated_at to track when upload finished
        asset.processing_status = "queued"
        asset.save(update_fields=["processing_status", "updated_at"])

        msg = asset.message

        # 4. Notify Receiver: "Upload Done, Processing Started..."
        payload = {
            "type": "chat_message",
            "success": True,
            "message": "Media upload completed, processing started",
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "status": msg.status,                 # still "pending"
                "processing_status": "queued",
                
                # --- UI INSTRUCTION: SHOW "PROCESSING..." ---
                "stage": "processing",                
                # --------------------------------------------

                "file_name": asset.file_name,
                "file_size": asset.file_size,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content_type": asset.content_type,
            },
        }
        notify_message_event.delay(payload)

        # 5. TRIGGER THE WORKER
        # This worker will eventually set status="sent" and stage="done"
        process_uploaded_asset.delay(asset.id)

        return Response({"success": True, "message": "Upload completed, processing started."}, status=200)