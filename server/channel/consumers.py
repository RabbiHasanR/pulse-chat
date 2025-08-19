import json
from channels.generic.websocket import AsyncWebsocketConsumer
from users.models import ChatUser
from chats.models import ChatMessage
from django.utils import timezone

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.receiver_id = self.scope['url_route']['kwargs']['receiver_id']
        self.sender = self.scope['user']
        self.room_name = f"chat_{min(self.sender.id, int(self.receiver_id))}_{max(self.sender.id, int(self.receiver_id))}"
        self.room_group_name = f"chat_{self.room_name}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data['message']

        receiver = await ChatUser.objects.aget(id=self.receiver_id)

        chat_message = await ChatMessage.objects.acreate(
            sender=self.sender,
            receiver=receiver,
            content=message,
            message_type='text',
            status='sent',
            created_at=timezone.now()
        )

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'sender_id': self.sender.id,
                'receiver_id': receiver.id,
                'timestamp': str(chat_message.created_at)
            }
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))
