from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Case, When, F

from asgiref.sync import async_to_sync

from utils.response import success_response, error_response
from utils.aws import s3, AWS_BUCKET, new_object_key
from .models import ChatMessage, MediaAsset
from .serializers import (
    CompleteUploadIn,
    ChatListSerializer,
    SendMessageInSerializer,
    SignBatchInSerializer,
    ForwardMessageInSerializer,
    ChatMessageListSerializer
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
from utils.s3 import DEFAULT_EXPIRES_PART

User = get_user_model()
    

class SendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = SendMessageInSerializer(data=request.data)
        if not ser.is_valid():
            return error_response(message="Invalid data", errors=ser.errors, status=400)
        
        data = ser.validated_data
        user = request.user
        receiver_id = data['receiver_id']
        text = data.get('text', '') 
        reply_to_id = data.get('reply_to_id')
        attachments = data.get('attachments', [])

        if attachments:
            msg, upload_instructions = ChatService.initialize_media_message(
                sender=user,
                receiver_id=receiver_id,
                text_caption=text,
                attachments=attachments,
                reply_to_id=reply_to_id
            )
            
            return success_response(
                message="Media initialized",
                data={
                    "message_id": msg.id,
                    "conversation_id": msg.conversation_id,
                    "type": "media",
                    "status": msg.status,
                    "created_at": msg.created_at,
                    "uploads": upload_instructions 
                },
                status=201
            )

        else:
            msg = ChatService.send_text_message(
                sender=user,
                receiver_id=receiver_id,
                content=text,
                reply_to_id=reply_to_id
            )


            return success_response(
                message="Message sent",
                data={
                    "message_id": msg.id,
                    "conversation_id": msg.conversation_id,
                    "type": "text",
                    "status": msg.status,
                    "created_at": msg.created_at,
                },
                status=201
            )
            
            
            

class CompleteUpload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CompleteUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(ser.errors, status=400)
            
        d = ser.validated_data
        
        try:
            asset = MediaAsset.objects.get(id=d['asset_id'], processing_status="queued")
        except MediaAsset.DoesNotExist:
            return error_response("Asset not found or already processed", status=404)

        if d.get("parts") and d.get("upload_id"):
             s3.complete_multipart_upload(
                 Bucket=asset.bucket, 
                 Key=asset.object_key, 
                 UploadId=d["upload_id"], 
                 MultipartUpload={"Parts": d["parts"]}
             )

        if asset.kind == MediaAsset.Kind.VIDEO:
            process_video_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.IMAGE:
            process_image_task.delay(asset.id)
        elif asset.kind == MediaAsset.Kind.AUDIO:
            process_audio_task.delay(asset.id)
        else:
            process_file_task.delay(asset.id)

        return success_response("Upload completed")
    

class SignBatchView(APIView):

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

        queryset = Conversation.objects.filter(
            Q(participant_1=user_id) | Q(participant_2=user_id)
        ).annotate(
            partner_id=Case(
                When(participant_1=user_id, then=F('participant_2')),
                default=F('participant_1')
            )
        ).order_by('-updated_at')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        if page is not None:
            partner_ids = [getattr(obj, 'partner_id') for obj in page]

            user_objects = User.objects.filter(id__in=partner_ids)
            user_map = {u.id: u for u in user_objects}

            online_status_map = {}
            if partner_ids:
                online_status_map = async_to_sync(ChatRedisService.subscribe_and_get_presences)(
                    observer_id=user_id,
                    target_ids=partner_ids
                )


            serializer = ChatListSerializer(
                page, 
                many=True, 
                context={
                    'request': request,
                    'online_status_map': online_status_map,
                    'user_map': user_map
                }
            )
            
            return paginator.get_paginated_response(serializer.data)

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


        queryset = ChatMessage.objects.filter(
            conversation=conversation
        ).exclude(
            sender_id=partner_id, 
            status=ChatMessage.Status.FAILED
        ).select_related('reply_to').prefetch_related('media_assets').order_by('-created_at')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        latest_msg_id = None
        is_first_page = request.query_params.get('cursor') is None
        
        if is_first_page and page:
            top_msg = page[0]

            if top_msg.sender_id == partner_id:
                latest_msg_id = top_msg.id

        if latest_msg_id:
            ChatService.mark_messages_as_read(
                request.user, 
                conversation, 
                partner_id, 
                latest_message_id=latest_msg_id
            )
        
        serializer = ChatMessageListSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)