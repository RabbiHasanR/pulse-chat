from rest_framework import serializers
from .models import ChatUser, Contact

class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatUser
        fields = ['email', 'username', 'full_name']

    def create(self, validated_data):
        user = ChatUser.objects.create_user(**validated_data)
        return user



class ContactUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatUser
        fields = ['id', 'username', 'email', 'full_name', 'avatar_url']

class ContactSerializer(serializers.ModelSerializer):
    contact_user = ContactUserSerializer()

    class Meta:
        model = Contact
        fields = ['contact_user']
        
        
        



class InitAvatarUploadIn(serializers.Serializer):
    file_name = serializers.CharField()
    content_type = serializers.CharField()
    file_size = serializers.IntegerField()

    def validate_content_type(self, value):
        # 1. Allow ANY image format (jpeg, png, gif, webp, heic, etc.)
        if not value.startswith("image/"):
            raise serializers.ValidationError("Invalid format. File must be an image.")
        
        # 2. SECURITY BLOCK: SVG
        # SVGs can contain executable Javascript (XSS attacks). 
        # Never allow users to upload SVGs as avatars.
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
    
    def validate_object_key(self, value):
        if ".." in value or value.startswith("/"):
            raise serializers.ValidationError("Invalid key format.")
        return value