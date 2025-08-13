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
        fields = ['id', 'username', 'email', 'full_name']

class ContactSerializer(serializers.ModelSerializer):
    contact_user = ContactUserSerializer()

    class Meta:
        model = Contact
        fields = ['contact_user']