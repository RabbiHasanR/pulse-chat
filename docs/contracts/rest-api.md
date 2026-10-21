# REST API Contract

**Base URL**: `http://<host>:8000/api`  
**Authentication**: HTTP Bearer Header: `Authorization: Bearer <access_token>` (HS512 JWT)  
**Client Binding**: Requests must originate from the client IP and User-Agent that originally received the token.

---

## Response Envelope Format

All REST responses use standard unified envelopes (`utils/response.py`).

### Success Envelope
```json
{
  "success": true,
  "message": "Operation successful",
  "data": { ... }
}
```

### Error Envelope
```json
{
  "success": false,
  "message": "Error summary",
  "errors": {
    "field_name": ["Specific validation error"]
  }
}
```

### Cursor Paginated Envelope (`utils/pagination.py`)
```json
{
  "success": true,
  "message": "Resource retrieved successfully",
  "data": {
    "<resource_key>": [ ... ],
    "next": "http://localhost:8000/api/...?cursor=cD0yMDI2LTEwLTIx",
    "previous": null
  }
}
```

---

## Endpoints Index

### Authentication & Account (`/api/auth/`)

| Method | Endpoint | Spec | Auth | Description |
| --- | --- | --- | --- | --- |
| `POST` | `/api/auth/register/` | AUTH-001 | No | Create user account and trigger welcome email. |
| `POST` | `/api/auth/send-otp/` | AUTH-002 | No | Generate 6-digit OTP, send email, return short-lived email token. |
| `POST` | `/api/auth/verify-otp/` | AUTH-002 | No | Verify OTP code against email token; issue bound JWT token pair. |
| `POST` | `/api/auth/refresh-token/` | AUTH-003 | No | Refresh access token using bound refresh token. |
| `POST` | `/api/auth/logout/` | AUTH-004 | Yes | Blacklist refresh token to invalidate active session. |

### User Management & Contacts (`/api/auth/`)

| Method | Endpoint | Spec | Auth | Description |
| --- | --- | --- | --- | --- |
| `GET` | `/api/auth/me/` | USR-001 | Yes | Retrieve profile details of authenticated user. |
| `POST` | `/api/auth/add-contacts/` | USR-002 | Yes | Add another user to contacts by email or username. |
| `GET` | `/api/auth/get-contacts/` | USR-002 | Yes | Paginated contacts list with real-time online status. |
| `GET` | `/api/auth/explore-users/` | USR-003 | Yes | Paginated directory of registered users (excluding self). |
| `POST` | `/api/auth/avatar/` | USR-004 | Yes | Initialize presigned S3 PUT URL for avatar upload. |
| `PUT` | `/api/auth/avatar/` | USR-004 | Yes | Confirm uploaded avatar, copy to active key, and update profile. |

### Chat & Messaging (`/api/chat/`)

| Method | Endpoint | Spec | Auth | Description |
| --- | --- | --- | --- | --- |
| `POST` | `/api/chat/send/` | CHAT-001, CHAT-002 | Yes | Send text message or initialize media attachments upload. |
| `POST` | `/api/chat/sign-batch/` | CHAT-002 | Yes | Generate subsequent batch of presigned URLs for multipart uploads. |
| `POST` | `/api/chat/complete/` | CHAT-002 | Yes | Finalize S3 multipart upload and trigger async media processing. |
| `POST` | `/api/chat/forward/` | CHAT-003 | Yes | Bulk forward an existing message to up to 20 recipients. |
| `GET` | `/api/chat/list/` | CHAT-004 | Yes | Paginated list of conversations with partner profiles & badges. |
| `GET` | `/api/chat/user/<partner_id>/messages/` | CHAT-005 | Yes | Paginated message thread history; auto-marks unread messages seen. |
