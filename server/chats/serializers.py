from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Conversation
import math

User = get_user_model()

MIN_PART_SIZE = 5 * 1024 * 1024        # 5MB
MAX_PART_SIZE = 512 * 1024 * 1024      # 512MB (policy cap)
MAX_PARTS     = 10_000                 # S3 limit
DIRECT_THRESHOLD = 5 * 1024 * 1024     # 5MB
MAX_BATCH_COUNT  = 500

DEFAULT_EXPIRES_DIRECT = 300
DEFAULT_EXPIRES_PART = 3600

class AttachmentItem(serializers.Serializer):
    """
    Validates a single file within the album.
    """
    file_name = serializers.CharField()
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField()
    # "kind" helps frontend distinguish video vs image in the grid immediately
    kind = serializers.ChoiceField(choices=['image', 'video', 'audio', 'file']) 

    # Multipart fields (optional, only for big files)
    client_part_size = serializers.IntegerField(required=False, min_value=MIN_PART_SIZE, max_value=MAX_PART_SIZE)
    client_num_parts = serializers.IntegerField(required=False, min_value=1)

    def validate(self, d):
        file_size = d["file_size"]

        # 1. Direct Upload Logic (Small files)
        if file_size <= DIRECT_THRESHOLD:
            return d

        # 2. Multipart Upload Logic (Large files)
        # Must include chunking details
        for k in ("client_part_size", "client_num_parts"):
            if k not in d:
                raise serializers.ValidationError({k: "Required for files > 5MB"})

        cps = d["client_part_size"]
        cnp = d["client_num_parts"]

        expected = math.ceil(file_size / cps)
        if cnp != expected:
            raise serializers.ValidationError({
                "client_num_parts": f"Mismatch. Expected {expected} parts."
            })
        if cnp > MAX_PARTS:
            raise serializers.ValidationError({
                "client_num_parts": "Too many parts. Increase part size."
            })
        return d


class PrepareUploadIn(serializers.Serializer):
    # --- MODE A: NEW ALBUM CREATION ---
    receiver_id = serializers.IntegerField(required=False, min_value=1)
    text = serializers.CharField(required=False, allow_blank=True) # Caption for the album
    attachments = serializers.ListField(
        child=AttachmentItem(), 
        required=False, 
        allow_empty=False
    )

    # --- MODE B: NEXT-BATCH (For a single large file renewal) ---
    upload_id = serializers.CharField(required=False)
    object_key = serializers.CharField(required=False)
    start_part = serializers.IntegerField(required=False, min_value=1)
    batch_count = serializers.IntegerField(required=False, min_value=1, max_value=MAX_BATCH_COUNT)

    def validate(self, d):
        # Mode B: Renewing URLS for an existing upload
        if d.get("upload_id"):
            if not d.get("object_key"):
                raise serializers.ValidationError({"object_key": "Required with upload_id"})
            return d

        # Mode A: Creating new album
        if not d.get("receiver_id"):
            raise serializers.ValidationError({"receiver_id": "This field is required."})
        
        if not d.get("attachments"):
            raise serializers.ValidationError({"attachments": "At least one file is required."})

        return d

# CompleteUploadIn remains mostly the same, ensuring we identify assets correctly
class CompleteUploadIn(serializers.Serializer):
    object_key = serializers.CharField()
    upload_id = serializers.CharField(required=False)
    parts = serializers.ListField(child=serializers.DictField(), required=False)
    
    def validate(self, data):
        if data.get("parts") and not data.get("upload_id"):
            raise serializers.ValidationError("upload_id required for multipart completion")
        return data
    
    
    




class UserSimpleSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(source='avatar_url', read_only=True)
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'avatar'] 

class ChatListSerializer(serializers.ModelSerializer):
    partner = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id', 
            'updated_at', 
            'last_message_time', 
            'partner', 
            'is_online', 
            'last_message', 
            'unread_count'
        ]

    def get_partner(self, obj):
        partner_id = getattr(obj, 'partner_id', None)
        user_map = self.context.get('user_map', {})
        user = user_map.get(partner_id)
        
        if user:
            return UserSimpleSerializer(user).data
        return None

    def get_is_online(self, obj):
        status_map = self.context.get('online_status_map', {})
        partner_id = getattr(obj, 'partner_id', None)
        return status_map.get(partner_id, False)

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request:
            return 0
        
        user_id_str = str(request.user.id)
        counts = obj.unread_counts or {}
        return counts.get(user_id_str, 0)

    def get_last_message(self, obj):
        msg_type = obj.last_message_type
        content = obj.last_message_content
        
        if msg_type == 'text':
            if not content:
                return ""
            return content[:60] + "..." if len(content) > 60 else content
            
        elif msg_type == 'image':
            return "📷 Photo"
        elif msg_type == 'video':
            return "🎥 Video"
        elif msg_type == 'audio':
            return "🎤 Audio"
        elif msg_type == 'file':
            return "📁 File"
        elif msg_type == 'album':
            return "🖼️ Album"
            
        return ""