import math
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction

from .aws import s3, AWS_BUCKET, new_object_key
from .models import ChatMessage, MediaAsset
from .serializers import PrepareUploadIn, CompleteMultipartIn, CompleteDirectIn, DIRECT_THRESHOLD, MAX_BATCH_COUNT
from .tasks import process_media_asset
from .ws import notify_message_event

DEFAULT_EXPIRES_DIRECT = 1800  # 30 min
DEFAULT_EXPIRES_PART   = 3600  # 60 min

class PrepareUpload(APIView):
    """
    One endpoint:
      - First call (no upload_id): decide direct vs multipart.
      - Subsequent calls (with upload_id): return next batch of part URLs.
    """
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

        # Direct small file
        if file_size <= DIRECT_THRESHOLD:
            object_key = new_object_key(request.user.id, file_name)
            put_url = s3.generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": AWS_BUCKET, "Key": object_key, "ContentType": d["content_type"]},
                ExpiresIn=DEFAULT_EXPIRES_DIRECT,
            )
            with transaction.atomic():
                asset = MediaAsset.objects.create(
                    bucket=AWS_BUCKET, object_key=object_key, kind=d["message_type"],
                    content_type=d["content_type"], file_name=file_name,
                    file_size=file_size, processing_status="queued",
                )
                msg = ChatMessage.objects.create(
                    sender=request.user, receiver_id=d["receiver_id"], message_type=d["message_type"],
                    file_name=file_name, file_size=file_size, status="pending", media_asset=asset,
                )
            # Optional: notify UI upload_initiated
            notify_message_event(msg.id, "upload_initiated", {"object_key": object_key, "mode": "direct"})

            return Response({
                "mode": "direct",
                "object_key": object_key,
                "put_url": put_url,
                "expires_in": DEFAULT_EXPIRES_DIRECT,
                "message_id": msg.id,
            }, status=201)

        # Multipart large file (use client-provided sizes; already validated by serializer)
        cps = int(d["client_part_size"])
        cnp = int(d["client_num_parts"])
        num_parts = cnp
        part_size = cps

        object_key = new_object_key(request.user.id, file_name)
        create = s3.create_multipart_upload(
            Bucket=AWS_BUCKET,
            Key=object_key,
            ContentType=d["content_type"],
            ServerSideEncryption="AES256",
        )
        upload_id = create["UploadId"]

        with transaction.atomic():
            asset = MediaAsset.objects.create(
                bucket=AWS_BUCKET, object_key=object_key, kind=d["message_type"],
                content_type=d["content_type"], file_name=file_name,
                file_size=file_size, processing_status="queued",
            )
            msg = ChatMessage.objects.create(
                sender=request.user, receiver_id=d["receiver_id"], message_type=d["message_type"],
                file_name=file_name, file_size=file_size, status="pending", media_asset=asset,
            )

        notify_message_event(msg.id, "upload_initiated", {"object_key": object_key, "mode": "multipart", "upload_id": upload_id})

        # sign first batch (1..batch_count or until num_parts)
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
            "batch": {"start_part": start, "count": count, "expires_in": DEFAULT_EXPIRES_PART, "items": items}
        })


class CompleteMultipartUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteMultipartIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        res = s3.complete_multipart_upload(
            Bucket=AWS_BUCKET,
            Key=d["object_key"],
            UploadId=d["upload_id"],
            MultipartUpload={"Parts": d["parts"]},
        )

        asset = MediaAsset.objects.filter(object_key=d["object_key"]).first()
        if asset:
            asset.processing_status = "running"
            asset.save(update_fields=["processing_status"])
            msg = asset.chatmessage_set.first()
            if msg:
                msg.status = "sent"
                msg.save(update_fields=["status"])
                notify_message_event(msg.id, "upload_completed", {"location": res.get("Location")})

            # enqueue processing
            process_media_asset.delay(asset.id)

        return Response({"ok": True})


class CompleteDirectUpload(APIView):
    """
    Client calls this after finishing the single PUT (direct).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteDirectIn(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        asset = MediaAsset.objects.filter(object_key=d["object_key"]).first()
        if not asset:
            return Response({"detail": "asset not found"}, status=404)

        asset.processing_status = "running"
        asset.save(update_fields=["processing_status"])
        msg = asset.chatmessage_set.first()
        if msg:
            msg.status = "sent"
            msg.save(update_fields=["status"])
            notify_message_event(msg.id, "upload_completed", {"location": f"s3://{AWS_BUCKET}/{d['object_key']}"})
        process_media_asset.delay(asset.id)
        return Response({"ok": True})
