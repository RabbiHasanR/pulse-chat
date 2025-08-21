import json
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.receiver_id = self.scope['url_route']['kwargs']['receiver_id']
        self.sender = self.scope['user']
        await self.accept()

        if self.sender is None or self.sender.is_anonymous:
            await self.send(text_data=json.dumps({
                "success": False,
                "message": "Authentication failed",
                "errors": {"token": ["Invalid or missing access token"]}
            }))
            await self.close(code=4001)
            return

        self.room_name = f"chat_{min(self.sender.id, int(self.receiver_id))}_{max(self.sender.id, int(self.receiver_id))}"
        self.room_group_name = f"chat_{self.room_name}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # Mark sender as present in Redis (or in-memory store)
        self.presence_goup_name = f"presence_{self.room_group_name}"
        await self.channel_layer.group_send(
            self.presence_goup_name,
            {
                "type": "user_joined",
                "user_id": self.sender.id
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        from users.models import ChatUser
        from chats.models import ChatMessage
        data = json.loads(text_data)
        message = data['message']

        receiver = await ChatUser.objects.aget(id=self.receiver_id)

        # Step 1: Save message with 'sent' status
        chat_message = await ChatMessage.objects.acreate(
            sender=self.sender,
            receiver=receiver,
            content=message,
            message_type='text',
            status='sent',
            created_at=timezone.now()
        )
        
        sent_payload = {
            "success": True,
            "message": "Message Sent successfully",
            "data": {
                "content": message,
                "message_id": chat_message.id,
                "sender_id": self.sender.id,
                "receiver_id": receiver.id,
                "timestamp": str(chat_message.created_at),
                "status": "sent"
            }
        }

        # Step 2: Broadcast message to shared room
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'payload': sent_payload
            }
        )

        
        
        # Step 3: Notify receiver via personal channel
        await self.channel_layer.group_send(
            f"user_{receiver.id}",
            {
                'type': 'chat_message',
                'payload': sent_payload
            }
        )

        # Step 4: Check if receiver is in the room
        receiver_in_room = await self.is_user_in_room(receiver.id, self.presence_goup_name)

        if receiver_in_room:
            # Update DB status to 'seen'
            await ChatMessage.objects.filter(id=chat_message.id).aupdate(status='seen')
            
            status_payload = {
            "success": True,
            "message": "Status Updated",
            "data": {
                'message_id': chat_message.id,
                'status': 'seen',
                'receiver_id': receiver.id,
                'timestamp': str(timezone.now())
            }
        }
            # Notify sender of 'seen' status
            await self.channel_layer.group_send(
                f"user_{self.sender.id}",
                {
                    'type': 'message_status',
                    'payload': status_payload
                }
            )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def message_status(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def is_user_in_room(self, user_id, room_name):
        from utils.redis_client import redis_client
        return await redis_client.sismember(f"room:{room_name}:users", user_id)




class UserChannelConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user_id = int(self.scope['url_route']['kwargs']['user_id'])
        self.user = self.scope['user']

        await self.accept()
        if self.user is None or self.user.is_anonymous or self.user.id != self.user_id:
            await self.send(text_data=json.dumps({
                "success": False,
                "message": "Authentication failed",
                "errors": {"token": ["Invalid or missing access token"]}
            }))
            await self.close(code=4001)
            return

        self.room_group_name = f"user_{self.user_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

