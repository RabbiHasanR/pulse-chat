from rest_framework import serializers
from .models import ChatUser

class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatUser
        fields = ['email', 'username', 'full_name']

    def create(self, validated_data):
        user = ChatUser.objects.create_user(**validated_data)
        return user
