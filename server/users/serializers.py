import re
from rest_framework import serializers
from .models import ChatUser, Contact

_AVATAR_KEY_RE = re.compile(r'^avatars/temp/user_\d+_[a-f0-9]+\.\w+$')

class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatUser
        fields = ['email', 'username', 'full_name']

    def create(self, validated_data):
        user = ChatUser.objects.create_user(**validated_data)
        return user



class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatUser
        fields = ['id', 'username', 'email', 'full_name', 'avatar_url']

class ContactSerializer(serializers.ModelSerializer):
    contact_user = UserSerializer(read_only=True)
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = ['id', 'contact_user', 'is_online']

    def get_is_online(self, obj):
        status_map = self.context.get('online_status_map', {})
        return status_map.get(obj.contact_user_id, False)
        
        
        



class InitAvatarUploadIn(serializers.Serializer):
    file_name = serializers.CharField()
    content_type = serializers.CharField()
    file_size = serializers.IntegerField()

    def validate_content_type(self, value):
        if not value.startswith("image/"):
            raise serializers.ValidationError("Invalid format. File must be an image.")
        

        if value == "image/svg+xml":
             raise serializers.ValidationError("SVG images are not allowed for security reasons.")
             
        return value

    def validate_file_size(self, value):
        # LIMIT: 5MB
        # This is a generous limit for avatars.
        limit = 5 * 1024 * 1024 
        if value > limit:
            raise serializers.ValidationError(
                "Image too large. Please upload an image smaller than 5MB."
            )
        return value

class ConfirmAvatarUploadIn(serializers.Serializer):
    object_key = serializers.CharField()

    def validate_object_key(self, value: str) -> str:
        if not _AVATAR_KEY_RE.match(value):
            raise serializers.ValidationError("Invalid key format.")
        return value