import math
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction

from utils.aws import s3, AWS_BUCKET, new_object_key
from .models import ChatMessage, MediaAsset
from .serializers import ( PrepareUploadIn, DIRECT_THRESHOLD, MAX_BATCH_COUNT,
                          CompleteUploadIn)
from background_worker.chats.tasks import notify_message_event
# from .ws import notify_message_event

DEFAULT_EXPIRES_DIRECT = 1800  # 30 min
DEFAULT_EXPIRES_PART   = 3600  # 60 min

class PrepareUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PrepareUploadIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # NEXT-BATCH
        if d.get("upload_id"):
            return self._sign_batch(d)

        # FIRST CALL
        file_size = int(d["file_size"])
        file_name = d["file_name"]
        object_key = new_object_key(request.user.id, file_name)

        with transaction.atomic():
            # Create ChatMessage first
            msg = ChatMessage.objects.create(
                sender=request.user,
                receiver_id=d["receiver_id"],
                message_type=d["message_type"],
                file_name=file_name,
                file_size=file_size,
                status="pending",
            )

            # Create MediaAsset linked to message
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

        # Notify via Celery
        payload = {
            "type": "chat_message",
            "success": True,
            "message": "Media message update",
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "status": msg.status,
                "file_name": asset.file_name,
                "file_size": asset.file_size,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content_type": asset.content_type,
                "processing_status": asset.processing_status,
            },
        }
        notify_message_event.delay(payload)

        # Direct upload
        if file_size <= DIRECT_THRESHOLD:
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

        # Multipart upload
        cps = int(d["client_part_size"])
        cnp = int(d["client_num_parts"])
        num_parts = cnp
        part_size = cps

        create = s3.create_multipart_upload(
            Bucket=AWS_BUCKET,
            Key=object_key,
            ContentType=d["content_type"],
            ServerSideEncryption="AES256",
        )
        upload_id = create["UploadId"]

        batch_count = min(d.get("batch_count") or 100, MAX_BATCH_COUNT)
        items = []
        max_pn = min(num_parts, batch_count)
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
            "part_size": part_size,
            "num_parts": num_parts,
            "batch": {
                "start_part": 1,
                "count": max_pn,
                "expires_in": DEFAULT_EXPIRES_PART,
                "items": items,
            },
            "message_id": msg.id,
        }, status=201)

    def _sign_batch(self, d):
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

        # Find the asset
        try:
            asset = MediaAsset.objects.select_related("message").get(
                object_key=object_key,
                processing_status="queued"
            )
        except MediaAsset.DoesNotExist:
            return Response({"error": "Asset not found or already processed"}, status=404)

        # Complete multipart upload if parts are provided
        if parts:
            try:
                s3.complete_multipart_upload(
                    Bucket=asset.bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception as e:
                return Response({"error": str(e)}, status=400)

        # Mark asset as ready for processing
        asset.processing_status = "queued"
        asset.save(update_fields=["processing_status", "updated_at"])

        # Build payload from linked message
        msg = asset.message
        payload = {
            "type": "chat_message",
            "success": True,
            "message": "Media upload completed",
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "status": msg.status,
                "file_name": asset.file_name,
                "file_size": asset.file_size,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content_type": asset.content_type,
                "processing_status": asset.processing_status,
            },
        }
        notify_message_event.delay(payload)

        # Trigger post-processing task (optional)
        # process_uploaded_asset.delay(asset.id)

        return Response({"success": True, "message": "Upload completed and processing started."}, status=200)
