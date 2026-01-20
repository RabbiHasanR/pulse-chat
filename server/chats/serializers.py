from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Conversation, ChatMessage, MediaAsset
import math

User = get_user_model()

# ... Constants (MIN_PART_SIZE etc.) remain the same ...
MIN_PART_SIZE = 5 * 1024 * 1024
MAX_PART_SIZE = 512 * 1024 * 1024
MAX_PARTS     = 10_000
DIRECT_THRESHOLD = 5 * 1024 * 1024
MAX_BATCH_COUNT  = 500

class AttachmentItem(serializers.Serializer):
    """
    Validates a single file within the album.
    """
    file_name = serializers.CharField()
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField()
    kind = serializers.ChoiceField(choices=['image', 'video', 'audio', 'file']) 

    # Multipart fields
    client_part_size = serializers.IntegerField(required=False, min_value=MIN_PART_SIZE, max_value=MAX_PART_SIZE)
    client_num_parts = serializers.IntegerField(required=False, min_value=1)
    batch_count = serializers.IntegerField(required=False, min_value=1, max_value=MAX_BATCH_COUNT)

    def validate(self, d):
        file_size = d["file_size"]
        if file_size <= DIRECT_THRESHOLD:
            return d

        for k in ("client_part_size", "client_num_parts"):
            if k not in d:
                raise serializers.ValidationError({k: "Required for files > 5MB"})

        cps = d["client_part_size"]
        cnp = d["client_num_parts"]
        expected = math.ceil(file_size / cps)
        
        if cnp != expected:
            raise serializers.ValidationError({"client_num_parts": f"Mismatch. Expected {expected} parts."})
        if cnp > MAX_PARTS:
            raise serializers.ValidationError({"client_num_parts": "Too many parts."})
        return d

# --- UNIFIED SEND MESSAGE SERIALIZER ---
class SendMessageInSerializer(serializers.Serializer):
    receiver_id = serializers.IntegerField(min_value=1)
    text = serializers.CharField(required=False, allow_blank=True)
    reply_to_id = serializers.IntegerField(required=False, allow_null=True)
    
    # List of files (Optional)
    attachments = serializers.ListField(
        child=AttachmentItem(), 
        required=False, 
        allow_empty=True
    )

    def validate(self, attrs):
        has_text = bool(attrs.get('text') and attrs['text'].strip())
        has_files = bool(attrs.get('attachments') and len(attrs['attachments']) > 0)

        if not has_text and not has_files:
            raise serializers.ValidationError("Message must have either text or attachments.")
        return attrs


class SignBatchInSerializer(serializers.Serializer):
    """
    Used to get the next set of Presigned URLs for a large multipart upload.
    """
    upload_id = serializers.CharField(required=True)
    object_key = serializers.CharField(required=True)
    start_part = serializers.IntegerField(required=False, default=1, min_value=1)
    batch_count = serializers.IntegerField(required=False, default=100, min_value=1, max_value=MAX_BATCH_COUNT)
    
    
class ForwardMessageInSerializer(serializers.Serializer):
    message_id = serializers.IntegerField()
    # Limit to 20 users at once for synchronous safety
    receiver_ids = serializers.ListField(
        child=serializers.IntegerField(), 
        min_length=1, 
        max_length=20 
    )
    # Optional: User can add a comment to the forward
    text = serializers.CharField(required=False, allow_blank=True)
    

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
    
    
    



class MediaAssetSerializer(serializers.ModelSerializer):
    """
    Serializes attachments. 
    Uses the @property fields 'url' and 'thumbnail_url' from the model.
    """
    url = serializers.CharField(read_only=True)
    thumbnail_url = serializers.CharField(read_only=True)

    class Meta:
        model = MediaAsset
        fields = [
            'id', 'kind', 'url', 'thumbnail_url', 
            'width', 'height', 'duration_seconds', 
            'file_name', 'file_size'
        ]

# --- 2. CHAT MESSAGE SERIALIZER ---
class ChatMessageSerializer(serializers.ModelSerializer):
    media_assets = MediaAssetSerializer(many=True, read_only=True)
    is_me = serializers.SerializerMethodField()
    
    class Meta:
        model = ChatMessage
        fields = [
            'id', 
            'sender',          # Returns ID (Integer)
            'content', 
            'message_type',
            'status', 
            'created_at', 
            'is_edited', 
            'is_forwarded', 
            'forward_source_name',
            
            # Reply Data
            'reply_to',        # ID of parent
            'reply_metadata',  # Snapshot { "sender": "Alice", "preview": "..." }
            
            # Attachments
            'asset_count',     # Quick check for UI
            'media_assets',    # Full objects with URLs
            
            # Helper
            'is_me'
        ]

    def get_is_me(self, obj):
        request = self.context.get('request')
        if request:
            return obj.sender_id == request.user.id
        return False