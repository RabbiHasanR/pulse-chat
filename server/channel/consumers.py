import json
from channels.generic.websocket import AsyncWebsocketConsumer
from utils.redis_client import redis_client, RedisKeys
from background_worker.chats.tasks import mark_delivered_and_notify_senders

class UserSocketConsumer(AsyncWebsocketConsumer):
    
    # --- 1. HELPERS ---
    @staticmethod
    def _room(user_id: int) -> str:
        return f"user_{user_id}"

    # --- 2. CONNECTION LIFECYCLE ---
    async def connect(self):
        self.user = self.scope.get("user")
        
        # A. Auth Check
        if not self.user or self.user.is_anonymous:
            await self.accept()
            await self._send_json({"type": "auth_error", "message": "Unauthorized"})
            await self.close(code=4002)
            return

        # B. Join Channel Group (For receiving messages)
        self.room_group_name = self._room(self.user.id)
        await self.accept()
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # C. Local State (Track what this specific socket is viewing)
        self.current_viewing_id = None

        # D. Online Status Logic (Redis Set Strategy)
        # We use 'self.channel_name' as the unique ID for this connection
        connections_key = RedisKeys.active_connections(self.user.id)
        await redis_client.sadd(connections_key, self.channel_name)
        
        # Check total active connections. If 1, User just went Online.
        count = await redis_client.scard(connections_key)
        if count == 1:
            await redis_client.sadd(RedisKeys.ONLINE_USERS, self.user.id)
            await self._notify_my_audience("online")
            
            # Trigger Background Task (Delivery Reports)
            mark_delivered_and_notify_senders.delay(self.user.id)

    async def disconnect(self, close_code):
        # 1. Safety Check: If user never authenticated, do nothing
        if not getattr(self, "user", None) or self.user.is_anonymous:
            return

        # 2. Leave Group (Safely)
        # Only try to leave if we actually joined a group
        room_group = getattr(self, "room_group_name", None)
        if room_group:
            await self.channel_layer.group_discard(room_group, self.channel_name)

        # 3. Cleanup "Viewing" Status (Read Receipts)
        if getattr(self, "current_viewing_id", None):
            view_key = RedisKeys.viewing(self.user.id, self.current_viewing_id)
            await redis_client.srem(view_key, self.channel_name)

        # 4. Cleanup Online Status
        connections_key = RedisKeys.active_connections(self.user.id)
        await redis_client.srem(connections_key, self.channel_name)
        
        # If NO connections are left, mark User as Globally Offline
        remaining = await redis_client.scard(connections_key)
        if remaining == 0:
            await redis_client.srem(RedisKeys.ONLINE_USERS, self.user.id)
            await self._notify_my_audience("offline")
            await redis_client.delete(connections_key)

    # --- 3. INBOUND HANDLERS ---
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        event_type = data.get("type")

        if event_type == "ping":
            await self._handle_ping()
        elif event_type == "chat_open":
            await self._handle_chat_open(data)
        elif event_type == "chat_close":
            await self._handle_chat_close(data)
        elif event_type == "chat_typing":
            await self._handle_chat_typing(data)

    # --- 4. FEATURE HANDLERS ---
    async def _handle_ping(self):
        """Heartbeat to keep connection alive."""
        await self._send_json({"type": "pong"})

    async def _handle_chat_open(self, data: dict):
        """
        Client says: 'I opened chat with User X'.
        We track this socket (channel_name) as 'viewing' User X.
        """
        target_id = data.get("receiver_id")
        if not target_id: return

        # If switching chats, remove this socket from the OLD viewing set
        if self.current_viewing_id and self.current_viewing_id != target_id:
            old_key = RedisKeys.viewing(self.user.id, self.current_viewing_id)
            await redis_client.srem(old_key, self.channel_name)

        self.current_viewing_id = target_id
        new_key = RedisKeys.viewing(self.user.id, target_id)
        
        # Add THIS socket to the new viewing set
        await redis_client.sadd(new_key, self.channel_name)
        await redis_client.expire(new_key, 86400) 

    async def _handle_chat_close(self, data: dict):
        """Client says: 'I closed the chat window'."""
        target_id = data.get("receiver_id")
        if not target_id: return

        key = RedisKeys.viewing(self.user.id, target_id)
        await redis_client.srem(key, self.channel_name)
        
        if self.current_viewing_id == target_id:
            self.current_viewing_id = None

    async def _handle_chat_typing(self, data: dict):
        """Pass-through typing event to the receiver."""
        receiver_id = data.get("receiver_id")
        if receiver_id:
            await self._group_send(
                self._room(receiver_id), 
                "forward_event", 
                {
                    "type": "chat_typing", 
                    "data": {"sender_id": self.user.id, "receiver_id": receiver_id}
                }
            )

    # --- 5. NOTIFICATION LOGIC (Same as before) ---
    async def _notify_my_audience(self, status: str):
        my_audience_key = RedisKeys.presence_audience(self.user.id)
        audience_ids = await redis_client.smembers(my_audience_key)
        
        if not audience_ids: return

        payload = {
            "type": "presence_update",
            "data": {
                "user_id": self.user.id,
                "status": status
            }
        }
        
        for uid in audience_ids:
            await self._group_send(self._room(uid), "forward_event", payload)

    # --- 6. OUTBOUND HELPERS ---
    async def forward_event(self, event):
        await self._send_json(event["payload"])

    async def _send_json(self, payload: dict):
        await self.send(text_data=json.dumps(payload))

    async def _group_send(self, room: str, type_: str, payload: dict):
        await self.channel_layer.group_send(room, {"type": type_, "payload": payload})