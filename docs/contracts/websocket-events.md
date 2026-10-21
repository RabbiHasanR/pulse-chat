# WebSocket Event Contract

**Endpoint**: `ws://<host>:8000/ws/chat/?token=<access_jwt>`  
**Protocol Version**: 1.0  
**Transport**: JSON text frames over WebSocket.

---

## Connection Lifecycle

1. **Authentication**:
   Client passes Access JWT in the `token` query parameter.
   - If invalid or unauthenticated: Server transmits `auth_error` and closes socket with code `4002`.
   - If authenticated: Socket connection accepted; added to channel group `user_{user_id}`.
2. **Heartbeat**:
   Client periodically sends `{"type": "ping"}`. Server replies with `{"type": "pong"}`.
3. **Disconnection**:
   On disconnect, connection is removed from `user:{id}:connections`. If connection count reaches 0, user transitions to `offline` and audience is notified.

---

## Inbound Events (Client → Server)

### 1. `ping`
Keep-alive heartbeat.
```json
{
  "type": "ping"
}
```

### 2. `chat_open`
Notifies server that client is viewing a conversation with `receiver_id`. Enables automatic "seen" (blue tick) receipting.
```json
{
  "type": "chat_open",
  "receiver_id": 102
}
```

### 3. `chat_close`
Notifies server that client closed the active conversation.
```json
{
  "type": "chat_close",
  "receiver_id": 102
}
```

### 4. `chat_typing`
Emits an ephemeral typing indicator to the recipient.
```json
{
  "type": "chat_typing",
  "receiver_id": 102
}
```

---

## Outbound Events (Server → Client)

### 1. `auth_error`
Sent prior to connection rejection on invalid authentication.
```json
{
  "type": "auth_error",
  "message": "Unauthorized"
}
```

### 2. `pong`
Response to client heartbeat.
```json
{
  "type": "pong"
}
```

### 3. `presence_update`
Broadcast to users who have subscribed to the target user's presence audience.
```json
{
  "type": "presence_update",
  "data": {
    "user_id": 102,
    "status": "online"
  }
}
```
*`status` values*: `"online"`, `"offline"`.

### 4. `chat_typing`
Forwarded typing indicator from chat partner.
```json
{
  "type": "chat_typing",
  "data": {
    "sender_id": 101,
    "receiver_id": 102
  }
}
```

### 5. `chat_message_new`
Broadcast when a new text or media message is created. Emitted to **both** receiver group and sender group (multi-device sync).
```json
{
  "type": "chat_message_new",
  "data": {
    "id": 501,
    "conversation_id": 24,
    "sender": {
      "id": 101,
      "email": "alice@example.com",
      "full_name": "Alice Smith",
      "avatar_url": "https://chat-bucket.s3.amazonaws.com/avatars/active/alice.webp"
    },
    "content": "Hello there!",
    "message_type": "text",
    "status": "sent",
    "created_at": "2026-10-21T13:00:00Z",
    "is_edited": false,
    "is_forwarded": false,
    "forward_source_name": null,
    "reply_to": null,
    "reply_metadata": null,
    "asset_count": 0,
    "media_assets": [],
    "is_me": false
  }
}
```

### 6. `chat_message_update`
Emitted as media processing completes or progresses.
```json
{
  "type": "chat_message_update",
  "success": true,
  "data": {
    "id": 502,
    "conversation_id": 24,
    "status": "sent",
    "media_assets": [
      {
        "id": 89,
        "kind": "image",
        "processing_status": "done",
        "url": "https://chat-bucket.s3.amazonaws.com/processed/89/uuid_optimized.webp",
        "thumbnail_url": "https://chat-bucket.s3.amazonaws.com/processed/89/uuid_thumb.webp",
        "file_size": 245000,
        "width": 1920,
        "height": 1080,
        "duration_seconds": null,
        "progress": 100
      }
    ]
  }
}
```

### 7. `chat_delivery_receipt`
Notifies sender that messages up to `last_delivered_id` have been delivered to receiver's active device.
```json
{
  "type": "chat_delivery_receipt",
  "data": {
    "conversation_id": 24,
    "receiver_id": 102,
    "last_delivered_id": 502
  }
}
```

### 8. `chat_read_receipt`
Notifies partner that reader has opened and read messages up to `last_read_id`.
```json
{
  "type": "chat_read_receipt",
  "data": {
    "conversation_id": 24,
    "reader_id": 102,
    "last_read_id": 502
  }
}
```
