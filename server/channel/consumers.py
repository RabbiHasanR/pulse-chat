import json
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone


class UserSocketConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from utils.redis_client import redis_client
        from asgiref.sync import sync_to_async
        from django.db.models import Q
        from users.models import Contact
        from chats.models import ChatMessage
        
        self.user = self.scope['user']
        query_params = parse_qs(self.scope['query_string'].decode())
        self.token = query_params.get('token', [None])[0]
        self.tab_id = query_params.get('tab_id', [None])[0] or "mobile"
        # Fallback for mobile clients
        self.tab_id = self.tab_id or "mobile"

        if self.user is None or self.user.is_anonymous:
            await self.accept()
            await self.send(text_data=json.dumps({
                "type": "auth_error",
                "success": False,
                "message": "Authentication failed",
                "errors": {
                    "token": ["Missing or invalid"],
                    "tab_id": ["Missing or defaulted to mobile"]
                }
            }))
            await self.close(code=4002)
            return

        self.room_group_name = f"user_{self.user.id}"
        await self.accept()
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # Track tab/device presence
        tab_key = f"{self.token}:{self.tab_id}"
        await redis_client.sadd(f"user:{self.user.id}:active_tabs", tab_key)

        tab_count = await redis_client.scard(f"user:{self.user.id}:active_tabs")
        if tab_count == 1:
            await redis_client.sadd("online_users", self.user.id)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "presence_event",
                    "payload": {
                        "type": "presence_update",
                        "user_id": self.user.id,
                        "status": "online"
                    }
                }
            )
            
            # 🔍 Find all users who are connected to this user
            contact_ids = await sync_to_async(list)(
                Contact.objects.filter(contact_user=self.user).values_list("owner_id", flat=True).distinct()
            )
            message_ids = await sync_to_async(list)(
                ChatMessage.objects.filter(
                    Q(sender=self.user) | Q(receiver=self.user)
                ).values_list("sender_id", "receiver_id").distinct()
            )

            # Flatten and deduplicate
            related_ids = set(contact_ids) | {uid for pair in message_ids for uid in pair}
            related_ids.discard(self.user.id)  # exclude self

            # 🔔 Notify each related user
            for uid in related_ids:
                await self.channel_layer.group_send(
                    f"user_{uid}",
                    {
                        "type": "presence_event",
                        "payload": {
                            "type": "presence_update",
                            "user_id": self.user.id,
                            "status": "online"
                        }
                    }
                )

    async def disconnect(self, close_code):
        from utils.redis_client import redis_client
        from asgiref.sync import sync_to_async
        from django.db.models import Q
        from users.models import Contact
        from chats.models import ChatMessage

        if not hasattr(self, "user") or self.user is None or self.user.is_anonymous:
            return  # Skip cleanup if user is invalid

        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        tab_key = f"{self.token}:{self.tab_id}"
        await redis_client.srem(f"user:{self.user.id}:active_tabs", tab_key)

        await redis_client.delete(f"user:{self.user.id}:access:{self.token}:tab:{self.tab_id}:active_thread")

        tab_count = await redis_client.scard(f"user:{self.user.id}:active_tabs")
        if tab_count == 0:
            await redis_client.srem("online_users", self.user.id)

            await self.channel_layer.group_send(
                f"user_{self.user.id}",
                {
                    "type": "presence_event",
                    "payload": {
                        "type": "presence_update",
                        "user_id": self.user.id,
                        "status": "offline"
                    }
                }
            )

            contact_ids = await sync_to_async(list)(
                Contact.objects.filter(contact_user=self.user).values_list("owner_id", flat=True).distinct()
            )
            message_ids = await sync_to_async(list)(
                ChatMessage.objects.filter(
                    Q(sender=self.user) | Q(receiver=self.user)
                ).values_list("sender_id", "receiver_id").distinct()
            )

            related_ids = set(contact_ids) | {uid for pair in message_ids for uid in pair}
            related_ids.discard(self.user.id)

            for uid in related_ids:
                await self.channel_layer.group_send(
                    f"user_{uid}",
                    {
                        "type": "presence_event",
                        "payload": {
                            "type": "presence_update",
                            "user_id": self.user.id,
                            "status": "offline"
                        }
                    }
                )


    async def receive(self, text_data):
        from asgiref.sync import sync_to_async
        from users.models import ChatUser
        from chats.models import ChatMessage
        from utils.redis_client import redis_client
  

        data = json.loads(text_data)
        event_type = data.get("type")
        tab_id = self.tab_id or "mobile"
        token = self.token

        if event_type == "chat_message":
            message = data.get("message")
            receiver_id = data.get("receiver_id")
            if not message or not receiver_id:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "success": False,
                    "message": "Invalid payload"
                }))
                return

            receiver = await ChatUser.objects.aget(id=receiver_id)

            chat_message = await ChatMessage.objects.acreate(
                sender=self.user,
                receiver=receiver,
                content=message,
                message_type='text',
                status='sent',
                created_at=timezone.now()
            )

            payload = {
                "type": "chat_message",
                "success": True,
                "message": "Message sent",
                "data": {
                    "message_id": chat_message.id,
                    "content": message,
                    "sender_id": self.user.id,
                    "receiver_id": receiver.id,
                    "timestamp": str(chat_message.created_at),
                    "status": "sent"
                }
            }

            await self.channel_layer.group_send(f"user_{receiver.id}", {
                "type": "chat_event",
                "payload": payload
            })

            await self.channel_layer.group_send(f"user_{self.user.id}", {
                "type": "chat_event",
                "payload": payload
            })

            # Seen logic
            receiver_online = await redis_client.sismember("online_users", receiver.id)
            pattern = f"user:{receiver.id}:access:*:tab:*:active_thread"
            keys = await redis_client.keys(pattern)
            values = await redis_client.mget(*keys) if keys else []
            decoded = [v.decode() for v in values if v]
            is_viewing = str(self.user.id) in decoded

            if receiver_online:
                if is_viewing:
                    await ChatMessage.objects.filter(id=chat_message.id).aupdate(status='seen')
                    status_payload = {
                        "type": "message_status",
                        "success": True,
                        "message": "Message seen",
                        "data": {
                            "message_id": chat_message.id,
                            "status": "seen",
                            "receiver_id": receiver.id,
                            "timestamp": str(timezone.now())
                        }
                    }
                    await self.channel_layer.group_send(f"user_{self.user.id}", {
                        "type": "status_event",
                        "payload": status_payload
                    })
                else:
                    unseen_count = await ChatMessage.objects.filter(
                        sender_id=self.user.id,
                        receiver_id=receiver.id,
                        status='sent'
                    ).acount()

                    last_message = await ChatMessage.objects.filter(
                        sender_id=self.user.id,
                        receiver_id=receiver.id
                    ).order_by('-created_at').afirst()

                    summary_payload = {
                        "type": "chat_summary",
                        "success": True,
                        "message": "Unread message summary",
                        "data": {
                            "sender_id": self.user.id,
                            "receiver_id": receiver.id,
                            "unread_count": unseen_count,
                            "last_message": {
                                "message_id": last_message.id,
                                "content": last_message.content,
                                "timestamp": str(last_message.created_at)
                            }
                        }
                    }

                    await self.channel_layer.group_send(f"user_{receiver.id}", {
                        "type": "chat_event",
                        "payload": summary_payload
                    })

        elif event_type == "chat_typing":
            receiver_id = data.get("receiver_id")
            if receiver_id:
                typing_payload = {
                    "type": "chat_typing",
                    "success": True,
                    "data": {
                        "sender_id": self.user.id,
                        "receiver_id": receiver_id,
                        "timestamp": str(timezone.now())
                    }
                }
                await self.channel_layer.group_send(f"user_{receiver_id}", {
                    "type": "chat_event",
                    "payload": typing_payload
                })

        elif event_type == "chat_open":
            receiver_id = data.get("receiver_id")
            if receiver_id and token and tab_id:
                redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
                await redis_client.set(redis_key, str(receiver_id), ex=30)

                await ChatMessage.objects.filter(
                    sender_id=receiver_id,
                    receiver_id=self.user.id,
                    status__in=["sent", "delivered"]
                ).aupdate(status="seen")

                seen_messages = await sync_to_async(list)(
                    ChatMessage.objects.filter(
                        sender_id=receiver_id,
                        receiver_id=self.user.id,
                        status="seen"
                    ).values("id", "sender_id")
                )

                seen_payload = {
                    "type": "message_status_batch",
                    "success": True,
                    "message": "Messages marked as seen",
                    "data": [
                        {
                            "message_id": msg["id"],
                            "sender_id": msg["sender_id"],
                            "status": "seen"
                        }
                        for msg in seen_messages
                    ]
                }

                await self.channel_layer.group_send(f"user_{receiver_id}", {
                    "type": "status_event",
                    "payload": seen_payload
                })

                await self.send(text_data=json.dumps({
                    "type": "chat_open_ack",
                    "success": True,
                    "message": "Chat opened"
                }))

        elif event_type == "chat_close":
            receiver_id = data.get("receiver_id")
            if token and tab_id:
                redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
                current = await redis_client.get(redis_key)
                if current and current.decode() == str(receiver_id):
                    await redis_client.delete(redis_key)

                await self.send(text_data=json.dumps({
                    "type": "chat_close_ack",
                    "success": True,
                    "message": "Chat closed"
                }))

        elif event_type == "heartbeat":
            if token and tab_id:
                redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
                await redis_client.expire(redis_key, 30)

    async def chat_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def status_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

    async def presence_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

