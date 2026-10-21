# 📖 Real-Time Presence & Chat Consumer Workflow

## **1. Overview**

The `UserSocketConsumer` is the **central nervous system** of your real-time chat application. It manages a persistent WebSocket connection for every active tab or device a user has open.

**Key Features:**

1. **Multi-Device Synchronization (Echo):** Messages sent from one device (e.g., Desktop) are instantly broadcast to the user's other devices (e.g., Mobile) to keep chat history in sync.
2. **Global Online/Offline Status:** A user is "Online" if they have *at least one* active connection.
3. **Context-Aware Read Receipts:** Tracks exactly which conversation a user is looking at to trigger "Blue Ticks" instantly.
4. **Instant UI Rendering:** Message payloads include the sender's avatar and name, allowing the frontend to render new conversations without extra API calls.
5. **Smart Subscriptions:** Viewing the Contact List automatically subscribes a user to real-time status updates from their friends.

---

## **2. Architecture Diagram**

* **Django Channels:** Manages the persistent WebSocket connections.
* **Redis (Sets):** Stores the list of active socket IDs (`channel_name`) for each user.
* **Redis (Pub/Sub):** Broadcasts events like "User X is Online" or "New Message".

---

## **3. How Redis is Connected**

The consumer uses `server/utils/redis_client.py` to talk to Redis. We use **Redis Sets** for uniqueness and atomic counting.

| Redis Key Pattern | Type | Purpose |
| --- | --- | --- |
| `user:{id}:connections` | **Set** | Stores the unique `channel_name` of every active tab. <br>

<br>*(Size > 0 = Online)* |
| `user:{id}:viewing:{target_id}` | **Set** | Stores `channel_name` of tabs looking at a specific chat. <br>

<br>*(Size > 0 = Message Read Instantly)* |
| `user:{id}:presence_audience` | **Set** | List of User IDs who should be notified when this user goes Online/Offline. |
| `online_users` | **Set** | A global set of all User IDs currently online (used for quick API checks). |

---

## **4. Detailed Workflow**

### **A. Connection Lifecycle (The "Handshake")**

When a user opens your app:

1. **Auth Check:** The consumer verifies the JWT Token in the query params (`?token=...`). If invalid, the connection closes.
2. **Group Join:** The socket joins the room `user_{id}`. This is the "mailbox" where the server sends push notifications.
3. **Redis Registration:**
* The unique socket ID (`self.channel_name`) is added to `user:{id}:connections`.
* **Logic:** Checks if this is the user's *first* connection (`SCARD == 1`).
* **Result:**
* **If First Device:** Triggers `_notify_my_audience("online")` to alert friends.
* **If Secondary Device:** Connects silently (user is already online).





### **B. The "Chat Open" Event (Read Receipts)**

When the user clicks on a conversation with "User B":

1. **Frontend:** Sends `{ "type": "chat_open", "receiver_id": 5 }`.
2. **Consumer:**
* Removes this tab's ID from any previous "viewing" sets.
* Adds `self.channel_name` to `user:{me}:viewing:{5}`.


3. **Result:** If User B sends a message now, the backend sees you are viewing and marks the message as **READ** instantly.

### **C. The "Chat Close" Event**

When the user navigates back to the chat list or minimizes the app:

1. **Frontend:** Sends `{ "type": "chat_close", "receiver_id": 5 }`.
2. **Consumer:** Removes `self.channel_name` from `user:{me}:viewing:{5}`.
3. **Result:** Subsequent messages will be "Delivered" (Grey Ticks).

### **D. Disconnection (Closing a Tab)**

When the user closes the tab:

1. **Cleanup Viewing:** Removes `channel_name` from any viewing sets.
2. **Cleanup Connection:** Removes `channel_name` from `user:{id}:connections`.
3. **Offline Logic:**
* Checks remaining connection count.
* **If Count == 0:** This was the last device. Triggers `_notify_my_audience("offline")`.



---

## **5. API Event Contract (Frontend Docs)**

### **Outbound (Server  Client)**

These events arrive via WebSocket.

#### **1. New Message (Receiver & Sender Echo)**

Sent to **both** the receiver and the sender (on all devices).

```json
{
    "type": "chat_message_new",
    "data": {
        "id": 105,
        "content": "Hello World",
        "message_type": "text",
        "created_at": "2024-01-30T10:00:00Z",
        "status": "sent",
        "sender": {
            "id": 2,
            "full_name": "Alice Wonderland",
            "avatar": "https://s3.aws.../avatar.jpg",
            "username": "@alice"
        },
        "media_assets": []
    }
}

```

#### **2. Presence Update**

Sent when a friend comes online or goes offline.

```json
{
    "type": "presence_update",
    "data": { 
        "user_id": 5, 
        "status": "online" // or "offline"
    }
}

```

#### **3. Read Receipt**

Sent to the *sender* when the recipient reads their messages.

```json
{
    "type": "chat_read_receipt",
    "data": {
        "conversation_id": 12,
        "reader_id": 5,
        "last_read_id": 105 // All messages <= 105 are read
    }
}

```

### **Inbound (Client  Server)**

Send these JSON frames to the server.

| Event Type | Payload Example | When to send |
| --- | --- | --- |
| `chat_open` | `{ "type": "chat_open", "receiver_id": 5 }` | User enters the chat screen. |
| `chat_close` | `{ "type": "chat_close", "receiver_id": 5 }` | User leaves the chat screen. |
| `chat_typing` | `{ "type": "chat_typing", "receiver_id": 5 }` | User is typing in the input box. |
| `ping` | `{ "type": "ping" }` | Every 30s to keep connection alive. |

---

## **6. Backend Integration Points**

### **How the API talks to WebSockets**

When you use the REST API (`POST /messages`), it uses `server/chats/services.py` to inject events into the socket layer:

1. **Service:** `ChatService.send_text_message(...)`
2. **Action:** Saves to DB.
3. **Broadcast:** Calls `_broadcast_message(sender_id, receiver_id, msg)`.
4. **Channel Layer:** Pushes the `chat_message_new` payload to `group:user_{sender_id}` AND `group:user_{receiver_id}`.

### **How Contact List Subscribes Users**

When you call `GET /api/users/contacts/`:

1. **View:** Calls `ChatRedisService.subscribe_and_get_presences(...)`.
2. **Redis:** Adds your ID to the `presence_audience` set of every contact in the list.
3. **Result:** You immediately get future "Online/Offline" alerts for these people without needing to send manual socket commands.