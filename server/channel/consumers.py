# import json
# from urllib.parse import parse_qs
# from channels.generic.websocket import AsyncWebsocketConsumer
# from django.db.models import Q
# from django.utils import timezone
# from asgiref.sync import sync_to_async
# from users.models import ChatUser, Contact
# from chats.models import ChatMessage
# from utils.redis_client import redis_client


# class UserSocketConsumer(AsyncWebsocketConsumer):
#     async def connect(self):
#         self.user = self.scope['user']
#         query_params = parse_qs(self.scope['query_string'].decode())
#         self.token = query_params.get('token', [None])[0]
#         self.tab_id = query_params.get('tab_id', [None])[0] or "mobile"
#         # Fallback for mobile clients
#         self.tab_id = self.tab_id or "mobile"

#         if self.user is None or self.user.is_anonymous:
#             await self.accept()
#             await self.send(text_data=json.dumps({
#                 "type": "auth_error",
#                 "success": False,
#                 "message": "Authentication failed",
#                 "errors": {
#                     "token": ["Missing or invalid"],
#                     "tab_id": ["Missing or defaulted to mobile"]
#                 }
#             }))
#             await self.close(code=4002)
#             return

#         self.room_group_name = f"user_{self.user.id}"
#         await self.accept()
#         await self.channel_layer.group_add(self.room_group_name, self.channel_name)

#         # Track tab/device presence
#         tab_key = f"{self.token}:{self.tab_id}"
#         await redis_client.sadd(f"user:{self.user.id}:active_tabs", tab_key)

#         tab_count = await redis_client.scard(f"user:{self.user.id}:active_tabs")
#         if tab_count == 1:
#             await redis_client.sadd("online_users", self.user.id)
#             await self.channel_layer.group_send(
#                 self.room_group_name,
#                 {
#                     "type": "presence_event",
#                     "payload": {
#                         "type": "presence_update",
#                         "user_id": self.user.id,
#                         "status": "online"
#                     }
#                 }
#             )
            
#             # 🔍 Find all users who are connected to this user
#             contact_ids = await sync_to_async(list)(
#                 Contact.objects.filter(contact_user=self.user).values_list("owner_id", flat=True).distinct()
#             )
#             message_ids = await sync_to_async(list)(
#                 ChatMessage.objects.filter(
#                     Q(sender=self.user) | Q(receiver=self.user)
#                 ).values_list("sender_id", "receiver_id").distinct()
#             )

#             # Flatten and deduplicate
#             related_ids = set(contact_ids) | {uid for pair in message_ids for uid in pair}
#             related_ids.discard(self.user.id)  # exclude self

#             # 🔔 Notify each related user
#             for uid in related_ids:
#                 await self.channel_layer.group_send(
#                     f"user_{uid}",
#                     {
#                         "type": "presence_event",
#                         "payload": {
#                             "type": "presence_update",
#                             "user_id": self.user.id,
#                             "status": "online"
#                         }
#                     }
#                 )

#     async def disconnect(self, close_code):

#         if not hasattr(self, "user") or self.user is None or self.user.is_anonymous:
#             return  # Skip cleanup if user is invalid

#         if hasattr(self, "room_group_name"):
#             await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

#         tab_key = f"{self.token}:{self.tab_id}"
#         await redis_client.srem(f"user:{self.user.id}:active_tabs", tab_key)

#         await redis_client.delete(f"user:{self.user.id}:access:{self.token}:tab:{self.tab_id}:active_thread")

#         tab_count = await redis_client.scard(f"user:{self.user.id}:active_tabs")
#         if tab_count == 0:
#             await redis_client.srem("online_users", self.user.id)

#             await self.channel_layer.group_send(
#                 f"user_{self.user.id}",
#                 {
#                     "type": "presence_event",
#                     "payload": {
#                         "type": "presence_update",
#                         "user_id": self.user.id,
#                         "status": "offline"
#                     }
#                 }
#             )

#             contact_ids = await sync_to_async(list)(
#                 Contact.objects.filter(contact_user=self.user).values_list("owner_id", flat=True).distinct()
#             )
#             message_ids = await sync_to_async(list)(
#                 ChatMessage.objects.filter(
#                     Q(sender=self.user) | Q(receiver=self.user)
#                 ).values_list("sender_id", "receiver_id").distinct()
#             )

#             related_ids = set(contact_ids) | {uid for pair in message_ids for uid in pair}
#             related_ids.discard(self.user.id)

#             for uid in related_ids:
#                 await self.channel_layer.group_send(
#                     f"user_{uid}",
#                     {
#                         "type": "presence_event",
#                         "payload": {
#                             "type": "presence_update",
#                             "user_id": self.user.id,
#                             "status": "offline"
#                         }
#                     }
#                 )


#     async def receive(self, text_data):

  

#         data = json.loads(text_data)
#         event_type = data.get("type")
#         tab_id = self.tab_id or "mobile"
#         token = self.token

#         if event_type == "chat_message":
#             message = data.get("message")
#             receiver_id = data.get("receiver_id")
#             if not message or not receiver_id:
#                 await self.send(text_data=json.dumps({
#                     "type": "error",
#                     "success": False,
#                     "message": "Invalid payload"
#                 }))
#                 return

#             receiver = await ChatUser.objects.aget(id=receiver_id)

#             chat_message = await ChatMessage.objects.acreate(
#                 sender=self.user,
#                 receiver=receiver,
#                 content=message,
#                 message_type='text',
#                 status='sent',
#                 created_at=timezone.now()
#             )

#             payload = {
#                 "type": "chat_message",
#                 "success": True,
#                 "message": "Message sent",
#                 "data": {
#                     "message_id": chat_message.id,
#                     "content": message,
#                     "sender_id": self.user.id,
#                     "receiver_id": receiver.id,
#                     "timestamp": str(chat_message.created_at),
#                     "status": "sent"
#                 }
#             }

#             await self.channel_layer.group_send(f"user_{receiver.id}", {
#                 "type": "chat_event",
#                 "payload": payload
#             })

#             await self.channel_layer.group_send(f"user_{self.user.id}", {
#                 "type": "chat_event",
#                 "payload": payload
#             })

#             # Seen logic
#             receiver_online = await redis_client.sismember("online_users", receiver.id)
#             pattern = f"user:{receiver.id}:access:*:tab:*:active_thread"
#             keys = await redis_client.keys(pattern)
#             values = await redis_client.mget(*keys) if keys else []
#             decoded = [v.decode() for v in values if v]
#             is_viewing = str(self.user.id) in decoded

#             if receiver_online:
#                 if is_viewing:
#                     await ChatMessage.objects.filter(id=chat_message.id).aupdate(status='seen')
#                     status_payload = {
#                         "type": "message_status",
#                         "success": True,
#                         "message": "Message seen",
#                         "data": {
#                             "message_id": chat_message.id,
#                             "status": "seen",
#                             "receiver_id": receiver.id,
#                             "sender_id": self.user.id,
#                             "timestamp": str(timezone.now())
#                         }
#                     }
#                     await self.channel_layer.group_send(f"user_{self.user.id}", {
#                         "type": "status_event",
#                         "payload": status_payload
#                     })
#                 else:
#                     unseen_count = await ChatMessage.objects.filter(
#                         sender_id=self.user.id,
#                         receiver_id=receiver.id,
#                         status='sent'
#                     ).acount()

#                     last_message = await ChatMessage.objects.filter(
#                         sender_id=self.user.id,
#                         receiver_id=receiver.id
#                     ).order_by('-created_at').afirst()

#                     summary_payload = {
#                         "type": "chat_summary",
#                         "success": True,
#                         "message": "Unread message summary",
#                         "data": {
#                             "sender_id": self.user.id,
#                             "receiver_id": receiver.id,
#                             "unread_count": unseen_count,
#                             "last_message": {
#                                 "message_id": last_message.id,
#                                 "content": last_message.content,
#                                 "timestamp": str(last_message.created_at)
#                             }
#                         }
#                     }

#                     await self.channel_layer.group_send(f"user_{receiver.id}", {
#                         "type": "chat_event",
#                         "payload": summary_payload
#                     })
            
        
#         elif event_type == "message_edit":
#             message_id = data.get("message_id")
#             new_content = data.get("new_content")

#             if not message_id or new_content is None:
#                 await self.send(text_data=json.dumps({
#                     "type": "error",
#                     "success": False,
#                     "message": "Missing message_id or new_content"
#                 }))
#                 return

#             message = await ChatMessage.objects.filter(id=message_id, sender=self.user).afirst()
#             if not message:
#                 await self.send(text_data=json.dumps({
#                     "type": "error",
#                     "success": False,
#                     "message": "Message not found or unauthorized"
#                 }))
#                 return

#             message.content = new_content
#             await sync_to_async(message.save)()

#             edit_payload = {
#                 "type": "message_edit",
#                 "success": True,
#                 "message": "Message updated",
#                 "data": {
#                     "sender_id": self.user.id,
#                     "receiver_id": message.receiver.id,
#                     "message_id": message.id,
#                     "new_content": message.content,
#                     "timestamp": str(message.updated_at)
#                 }
#             }

#             # Notify both sender and receiver
#             await self.channel_layer.group_send(f"user_{message.receiver.id}", {
#                 "type": "chat_event",
#                 "payload": edit_payload
#             })
#             await self.channel_layer.group_send(f"user_{self.user.id}", {
#                 "type": "chat_event",
#                 "payload": edit_payload
#             })
        
#         elif event_type == "message_delete":
#             message_id = data.get("message_id")

#             if not message_id:
#                 await self.send(text_data=json.dumps({
#                     "type": "error",
#                     "success": False,
#                     "message": "Missing message_id"
#                 }))
#                 return

#             message = await ChatMessage.objects.filter(id=message_id, sender=self.user).afirst()
#             if not message:
#                 await self.send(text_data=json.dumps({
#                     "type": "error",
#                     "success": False,
#                     "message": "Message not found or unauthorized"
#                 }))
#                 return

#             message.is_deleted = True
#             await sync_to_async(message.save)()

#             delete_payload = {
#                 "type": "message_delete",
#                 "success": True,
#                 "message": "Message deleted",
#                 "data": {
#                     "sender_id": self.user.id,
#                     "receiver_id": message.receiver.id,
#                     "message_id": message.id,
#                     "timestamp": str(message.updated_at)
#                 }
#             }

#             await self.channel_layer.group_send(f"user_{message.receiver.id}", {
#                 "type": "chat_event",
#                 "payload": delete_payload
#             })
#             await self.channel_layer.group_send(f"user_{self.user.id}", {
#                 "type": "chat_event",
#                 "payload": delete_payload
#             })

#         elif event_type == "chat_typing":
#             receiver_id = data.get("receiver_id")
#             if receiver_id:
#                 typing_payload = {
#                     "type": "chat_typing",
#                     "success": True,
#                     "data": {
#                         "sender_id": self.user.id,
#                         "receiver_id": receiver_id,
#                         "timestamp": str(timezone.now())
#                     }
#                 }
#                 await self.channel_layer.group_send(f"user_{receiver_id}", {
#                     "type": "chat_event",
#                     "payload": typing_payload
#                 })

#         elif event_type == "chat_open":
#             receiver_id = data.get("receiver_id")
#             if receiver_id and token and tab_id:
#                 redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
#                 await redis_client.set(redis_key, str(receiver_id), ex=30)

#                 await ChatMessage.objects.filter(
#                     sender_id=receiver_id,
#                     receiver_id=self.user.id,
#                     status__in=["sent", "delivered"]
#                 ).aupdate(status="seen")

#                 seen_messages = await sync_to_async(list)(
#                     ChatMessage.objects.filter(
#                         sender_id=receiver_id,
#                         receiver_id=self.user.id,
#                         status="seen"
#                     ).values("id", "sender_id")
#                 )

#                 seen_payload = {
#                     "type": "message_status_batch",
#                     "success": True,
#                     "message": "Messages marked as seen",
#                     "data": [
#                         {
#                             "message_id": msg["id"],
#                             "receiver_id": receiver_id,
#                             "sender_id": msg["sender_id"],
#                             "status": "seen"
#                         }
#                         for msg in seen_messages
#                     ]
#                 }

#                 await self.channel_layer.group_send(f"user_{receiver_id}", {
#                     "type": "status_event",
#                     "payload": seen_payload
#                 })

#                 await self.send(text_data=json.dumps({
#                     "type": "chat_open_ack",
#                     "success": True,
#                     "message": "Chat opened"
#                 }))

#         elif event_type == "chat_close":
#             receiver_id = data.get("receiver_id")
#             if token and tab_id:
#                 redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
#                 current = await redis_client.get(redis_key)
#                 if current and current.decode() == str(receiver_id):
#                     await redis_client.delete(redis_key)

#                 await self.send(text_data=json.dumps({
#                     "type": "chat_close_ack",
#                     "success": True,
#                     "message": "Chat closed"
#                 }))

#         elif event_type == "heartbeat":
#             if token and tab_id:
#                 redis_key = f"user:{self.user.id}:access:{token}:tab:{tab_id}:active_thread"
#                 await redis_client.expire(redis_key, 30)

#     async def chat_event(self, event):
#         await self.send(text_data=json.dumps(event["payload"]))

#     async def status_event(self, event):
#         await self.send(text_data=json.dumps(event["payload"]))

#     async def presence_event(self, event):
#         await self.send(text_data=json.dumps(event["payload"]))





import json
from typing import Iterable, List, Set, Tuple
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import ValidationError

from users.models import ChatUser, Contact
from chats.models import ChatMessage
from utils.redis_client import redis_client


class UserSocketConsumer(AsyncWebsocketConsumer):
    ONLINE_USERS_SET = "online_users"

    @staticmethod
    def _active_tabs_key(user_id: int) -> str:
        return f"user:{user_id}:active_tabs"

    @staticmethod
    def _active_thread_key(user_id: int, token: str, tab_id: str) -> str:
        return f"user:{user_id}:access:{token}:tab:{tab_id}:active_thread"

    @staticmethod
    def _room(user_id: int) -> str:
        return f"user_{user_id}"


    async def _send_json(self, payload: dict) -> None:
        await self.send(text_data=json.dumps(payload))

    async def _group_send(self, room: str, type_: str, payload: dict) -> None:
        await self.channel_layer.group_send(room, {"type": type_, "payload": payload})

    async def _presence_fanout(self, user_id: int, status: str) -> None:
        payload = {
            "type": "presence_update",
            "user_id": user_id,
            "status": status,
        }
        await self._group_send(self._room(user_id), "forward_event", payload)
        for uid in await self._fetch_related_user_ids(user_id):
            await self._group_send(self._room(uid), "forward_event", payload)

    async def _fetch_related_user_ids(self, user_id: int) -> Set[int]:
        contact_ids: List[int] = await sync_to_async(list)(
            Contact.objects.filter(contact_user_id=user_id)
            .values_list("owner_id", flat=True)
            .distinct()
        )
        message_pairs: List[Tuple[int, int]] = await sync_to_async(list)(
            ChatMessage.objects.filter(Q(sender_id=user_id) | Q(receiver_id=user_id))
            .values_list("sender_id", "receiver_id")
            .distinct()
        )
        related_ids: Set[int] = set(contact_ids) | {uid for a, b in message_pairs for uid in (a, b)}
        related_ids.discard(user_id)
        return related_ids

    async def _set_tab_presence(self, user_id: int, tab_key: str) -> int:
        await redis_client.sadd(self._active_tabs_key(user_id), tab_key)
        return await redis_client.scard(self._active_tabs_key(user_id))

    async def _clear_tab_presence(self, user_id: int, tab_key: str) -> int:
        await redis_client.srem(self._active_tabs_key(user_id), tab_key)
        return await redis_client.scard(self._active_tabs_key(user_id))

    async def _mark_online(self, user_id: int) -> None:
        await redis_client.sadd(self.ONLINE_USERS_SET, user_id)
        await self._presence_fanout(user_id, "online")

    async def _mark_offline(self, user_id: int) -> None:
        await redis_client.srem(self.ONLINE_USERS_SET, user_id)
        await self._presence_fanout(user_id, "offline")

    async def _auth_error_and_close(self) -> None:
        await self.accept()
        await self._send_json(
            {
                "type": "auth_error",
                "success": False,
                "message": "Authentication failed",
                "errors": {"token": ["Missing or invalid"], "tab_id": ["Missing or defaulted to mobile"]},
            }
        )
        await self.close(code=4002)


    # Required fields per inbound event
    SCHEMAS = {
    "chat_message": {"message": str, "receiver_id": int},
    "message_edit": {"message_id": int, "new_content": str},
    "message_delete": {"message_id": int},
    "chat_typing": {"receiver_id": int},
    "chat_open": {"receiver_id": int},
    "chat_close": {"receiver_id": int},
    }


    async def _bad_request(self, message: str, errors: dict | None = None) -> None:
        await self._send_json({
        "type": "error",
        "success": False,
        "message": message,
        **({"errors": errors} if errors else {}),
        })


    def _validate_event(self, data: dict, event_type: str) -> dict:
        errors: dict = {}
        if (data or {}).get("type") != event_type:
            errors["type"] = f"must be '{event_type}'"


        schema = self.SCHEMAS.get(event_type, {})
        cleaned: dict = {}
        for field, typ in schema.items():
            value = (data or {}).get(field)
            if value is None:
                errors[field] = "missing"
                continue
            if typ is int:
                try:
                    cleaned[field] = int(value)
                except (TypeError, ValueError):
                    errors[field] = "must be int"
            elif typ is str:
                s = str(value).strip()
                if s == "":
                    errors[field] = "cannot be empty"
                else:
                    cleaned[field] = s
            else:
                cleaned[field] = value


        if errors:
            raise ValidationError(errors)
        return cleaned

    async def connect(self):
        self.user = self.scope.get("user")
        query_params = parse_qs(self.scope.get("query_string", b"").decode())
        self.token = (query_params.get("token", [None])[0]) or None
        self.tab_id = (query_params.get("tab_id", [None])[0]) or "mobile"  # mobile fallback

        if not self.user or self.user.is_anonymous:
            return await self._auth_error_and_close()

        self.room_group_name = self._room(self.user.id)
        await self.accept()
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        tab_key = f"{self.token}:{self.tab_id}"
        tab_count = await self._set_tab_presence(self.user.id, tab_key)

        if tab_count == 1:
            await self._mark_online(self.user.id)

    async def disconnect(self, close_code):
        if not getattr(self, "user", None) or self.user.is_anonymous:
            return

        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        tab_key = f"{getattr(self, 'token', None)}:{getattr(self, 'tab_id', 'mobile')}"
        tab_count = await self._clear_tab_presence(self.user.id, tab_key)

        if getattr(self, "token", None) and getattr(self, "tab_id", None):
            await redis_client.delete(self._active_thread_key(self.user.id, self.token, self.tab_id))

        if tab_count == 0:
            await self._mark_offline(self.user.id)


    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return await self._send_json({"type": "error", "success": False, "message": "Invalid JSON"})

        event_type = data.get("type")
        handlers = {
            "chat_message": self._handle_chat_message,
            "message_edit": self._handle_message_edit,
            "message_delete": self._handle_message_delete,
            "chat_typing": self._handle_chat_typing,
            "chat_open": self._handle_chat_open,
            "chat_close": self._handle_chat_close,
            "heartbeat": self._handle_heartbeat,
        }
        handler = handlers.get(event_type)
        if handler:
            await handler(data)
        else:
            await self._send_json({"type": "error", "success": False, "message": "Unknown event type"})


    async def _handle_chat_message(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "chat_message")
        except self.ValidationError as e:
            return await self._bad_request("Invalid payload", e.errors)


        message = v["message"]
        receiver_id = v["receiver_id"]

        if not message or not receiver_id:
            return await self._send_json({"type": "error", "success": False, "message": "Invalid payload"})

        try:
            receiver = await ChatUser.objects.aget(id=receiver_id)
        except ChatUser.DoesNotExist:
            return await self._send_json({"type": "error", "success": False, "message": "Receiver not found"})

        chat_message = await ChatMessage.objects.acreate(
            sender=self.user,
            receiver=receiver,
            content=message,
            message_type="text",
            status="sent",
            created_at=timezone.now(),
        )

        payload = self._build_chat_message_payload(chat_message)
        await self._group_send(self._room(receiver.id), "forward_event", payload)
        await self._group_send(self._room(self.user.id), "forward_event", payload)

        if await redis_client.sismember(self.ONLINE_USERS_SET, receiver.id):
            if await self._is_user_viewing_me(receiver_id=receiver.id):
                await ChatMessage.objects.filter(id=chat_message.id).aupdate(status="seen")
                await self._notify_single_status(
                    message_id=chat_message.id,
                    receiver_id=receiver.id,
                    sender_id=self.user.id,
                    status="seen",
                )
            else:
                await self._send_unread_summary(to_user_id=receiver.id, from_user_id=self.user.id)

    async def _handle_message_edit(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "message_edit")
        except self.ValidationError as e:
            return await self._bad_request("Missing or invalid fields", e.errors)


        message_id = v["message_id"]
        new_content = v["new_content"]

        if not message_id or new_content is None:
            return await self._send_json({"type": "error", "success": False, "message": "Missing message_id or new_content"})

        message = await ChatMessage.objects.filter(id=message_id, sender=self.user).afirst()
        if not message:
            return await self._send_json({"type": "error", "success": False, "message": "Message not found or unauthorized"})

        message.content = new_content
        await sync_to_async(message.save)()

        payload = self._build_message_edit_payload(message)
        await self._group_send(self._room(message.receiver.id), "forward_event", payload)
        await self._group_send(self._room(self.user.id), "forward_event", payload)

    async def _handle_message_delete(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "message_delete")
        except self.ValidationError as e:
            return await self._bad_request("Missing or invalid fields", e.errors)


        message_id = v["message_id"]

        if not message_id:
            return await self._send_json({"type": "error", "success": False, "message": "Missing message_id"})

        message = await ChatMessage.objects.filter(id=message_id, sender=self.user).afirst()
        if not message:
            return await self._send_json({"type": "error", "success": False, "message": "Message not found or unauthorized"})

        message.is_deleted = True
        await sync_to_async(message.save)()

        payload = self._build_message_delete_payload(message)
        await self._group_send(self._room(message.receiver.id), "forward_event", payload)
        await self._group_send(self._room(self.user.id), "forward_event", payload)

    async def _handle_chat_typing(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "chat_typing")
        except self.ValidationError as e:
            return await self._bad_request("Missing or invalid fields", e.errors)


        receiver_id = v["receiver_id"]

        if not receiver_id:
            return
        await self._group_send(
            self._room(receiver_id),
            "forward_event",
            {
                "type": "chat_typing",
                "success": True,
                "data": {
                    "sender_id": self.user.id,
                    "receiver_id": receiver_id,
                    "timestamp": str(timezone.now()),
                },
            },
        )

    async def _handle_chat_open(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "chat_open")
        except self.ValidationError as e:
            return await self._bad_request("Missing or invalid fields", e.errors)


        receiver_id = v["receiver_id"]
        token = getattr(self, "token", None)
        tab_id = getattr(self, "tab_id", None) or "mobile"
        if not (receiver_id and token and tab_id):
            return

        redis_key = self._active_thread_key(self.user.id, token, tab_id)
        await redis_client.set(redis_key, str(receiver_id), ex=30)

        await ChatMessage.objects.filter(
            sender_id=receiver_id,
            receiver_id=self.user.id,
            status__in=["sent", "delivered"],
        ).aupdate(status="seen")

        seen_messages = await sync_to_async(list)(
            ChatMessage.objects.filter(
                sender_id=receiver_id,
                receiver_id=self.user.id,
                status="seen",
            ).values("id", "sender_id")
        )

        await self._group_send(
            self._room(receiver_id),
            "forward_event",
            {
                "type": "message_status_batch",
                "success": True,
                "message": "Messages marked as seen",
                "data": [
                    {
                        "message_id": m["id"],
                        "receiver_id": receiver_id,
                        "sender_id": m["sender_id"],
                        "status": "seen",
                    }
                    for m in seen_messages
                ],
            },
        )

        await self._send_json({"type": "chat_open_ack", "success": True, "message": "Chat opened"})

    async def _handle_chat_close(self, data: dict) -> None:
        try:
            v = self._validate_event(data, "chat_close")
        except self.ValidationError as e:
            return await self._bad_request("Missing or invalid fields", e.errors)


        receiver_id = v["receiver_id"]
        token = getattr(self, "token", None)
        tab_id = getattr(self, "tab_id", None) or "mobile"
        if not (token and tab_id):
            return

        redis_key = self._active_thread_key(self.user.id, token, tab_id)
        current = await redis_client.get(redis_key)
        if current and current.decode() == str(receiver_id):
            await redis_client.delete(redis_key)

        await self._send_json({"type": "chat_close_ack", "success": True, "message": "Chat closed"})

    async def _handle_heartbeat(self, _data: dict) -> None:
        token = getattr(self, "token", None)
        tab_id = getattr(self, "tab_id", None) or "mobile"
        if token and tab_id:
            await redis_client.expire(self._active_thread_key(self.user.id, token, tab_id), 30)


    def _build_chat_message_payload(self, chat_message: ChatMessage) -> dict:
        return {
            "type": "chat_message",
            "success": True,
            "message": "Message sent",
            "data": {
                "message_id": chat_message.id,
                "content": chat_message.content,
                "sender_id": chat_message.sender_id,
                "receiver_id": chat_message.receiver_id,
                "timestamp": str(chat_message.created_at),
                "status": chat_message.status,
            },
        }

    def _build_message_edit_payload(self, message: ChatMessage) -> dict:
        return {
            "type": "message_edit",
            "success": True,
            "message": "Message updated",
            "data": {
                "sender_id": message.sender_id,
                "receiver_id": message.receiver_id,
                "message_id": message.id,
                "new_content": message.content,
                "timestamp": str(message.updated_at),
            },
        }

    def _build_message_delete_payload(self, message: ChatMessage) -> dict:
        return {
            "type": "message_delete",
            "success": True,
            "message": "Message deleted",
            "data": {
                "sender_id": message.sender_id,
                "receiver_id": message.receiver_id,
                "message_id": message.id,
                "timestamp": str(message.updated_at),
            },
        }

    async def _is_user_viewing_me(self, receiver_id: int) -> bool:
        pattern = f"user:{receiver_id}:access:*:tab:*:active_thread"
        keys = await redis_client.keys(pattern)
        if not keys:
            return False
        values = await redis_client.mget(*keys)
        decoded = [v.decode() for v in values if v]
        return str(self.user.id) in decoded

    async def _notify_single_status(self, message_id: int, receiver_id: int, sender_id: int, status: str) -> None:
        payload = {
            "type": "message_status",
            "success": True,
            "message": f"Message {status}",
            "data": {
                "message_id": message_id,
                "status": status,
                "receiver_id": receiver_id,
                "sender_id": sender_id,
                "timestamp": str(timezone.now()),
            },
        }
        await self._group_send(self._room(sender_id), "forward_event", payload)

    async def _send_unread_summary(self, to_user_id: int, from_user_id: int) -> None:
        unseen_count = await ChatMessage.objects.filter(
            sender_id=from_user_id, receiver_id=to_user_id, status="sent"
        ).acount()

        last_message = await ChatMessage.objects.filter(
            sender_id=from_user_id, receiver_id=to_user_id
        ).order_by("-created_at").afirst()

        summary_payload = {
            "type": "chat_summary",
            "success": True,
            "message": "Unread message summary",
            "data": {
                "sender_id": from_user_id,
                "receiver_id": to_user_id,
                "unread_count": unseen_count,
                "last_message": {
                    "message_id": last_message.id if last_message else None,
                    "content": last_message.content if last_message else None,
                    "timestamp": str(last_message.created_at) if last_message else None,
                },
            },
        }
        await self._group_send(self._room(to_user_id), "forward_event", summary_payload)
        
    async def forward_event(self, event):
        await self._send_json(event["payload"])
