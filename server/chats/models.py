from django.db import models
from django.conf import settings



# class MediaAsset(models.Model):
#     class Kind(models.TextChoices):
#         IMAGE = "image", "Image"
#         VIDEO = "video", "Video"
#         AUDIO = "audio", "Audio"
#         FILE  = "file",  "File"

#     # Storage identity
#     bucket = models.CharField(max_length=255)
#     object_key = models.CharField(max_length=1024, unique=True, db_index=True)

#     # What it is
#     kind = models.CharField(max_length=10, choices=Kind.choices)
#     content_type = models.CharField(max_length=255, blank=True, null=True)

#     # Original file facts (source of truth)
#     file_name = models.CharField(max_length=255, blank=True, null=True)
#     file_size = models.BigIntegerField(blank=True, null=True)

#     # Optional media info
#     width = models.IntegerField(blank=True, null=True)
#     height = models.IntegerField(blank=True, null=True)
#     duration_seconds = models.FloatField(blank=True, null=True)

#     # Processing lifecycle
#     processing_status = models.CharField(
#         max_length=16,
#         default="queued",
#         choices=[
#             ("queued", "Queued"),
#             ("running", "Running"),
#             ("done", "Done"),
#             ("failed", "Failed"),
#         ],
#         db_index=True,
#     )
#     processing_progress = models.FloatField(default=0.0)  # 0..100

#     # Variants & URLs (thumbnails, HLS manifest, previews, etc.)
#     # Example:
#     # {
#     #   "image": {"thumbnail": "https://.../thumb.jpg", "web": "https://.../web.webp"},
#     #   "video": {"hls": {"manifest": "https://.../master.m3u8", "poster": "https://.../poster.jpg"}},
#     #   "file":  {"original": "s3://bucket/key", "compressed": "s3://bucket/key.zst"}
#     # }
#     variants = models.JSONField(default=dict, blank=True)

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         ordering = ["-created_at"]

#     def __str__(self):
#         return f"{self.kind} | {self.file_name or self.object_key}"


# class ChatMessage(models.Model):
#     class MsgType(models.TextChoices):
#         TEXT  = "text",  "Text"
#         IMAGE = "image", "Image"
#         VIDEO = "video", "Video"
#         AUDIO = "audio", "Audio"
#         FILE  = "file",  "File"

#     class Status(models.TextChoices):
#         PENDING   = "pending",   "Pending"
#         SENT      = "sent",      "Sent"
#         DELIVERED = "delivered", "Delivered"
#         SEEN      = "seen",      "Seen"

#     sender = models.ForeignKey(
#         settings.AUTH_USER_MODEL, related_name="sent_messages", on_delete=models.CASCADE
#     )
#     receiver = models.ForeignKey(
#         settings.AUTH_USER_MODEL, related_name="received_messages", on_delete=models.CASCADE
#     )

#     message_type = models.CharField(max_length=10, choices=MsgType.choices, default=MsgType.TEXT)
#     content = models.TextField(blank=True, null=True)  # text content for text messages

#     media_asset = models.ForeignKey(
#         MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages"
#     )

#     # Tiny, optional denormalized cache for super-fast UI (kept in sync by your worker)
#     # Example:
#     # {"thumbnail_url": "...", "hls_manifest_url": "...", "display_name": "VID_1234.mp4", "display_size": 12345}
#     render_cache = models.JSONField(default=dict, blank=True)

#     status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
#     is_deleted = models.BooleanField(default=False)

#     created_at = models.DateTimeField(auto_now_add=True, db_index=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         ordering = ["-created_at"]

#     def __str__(self):
#         return f"{self.sender_id}->{self.receiver_id} | {self.message_type} | {self.status}"
    
    


class ChatMessage(models.Model):
    class MsgType(models.TextChoices):
        TEXT  = "text",  "Text"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE  = "file",  "File"
        ALBUM = "album", "Album"

    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        SENT      = "sent",      "Sent"
        DELIVERED = "delivered", "Delivered"
        SEEN      = "seen",      "Seen"

    # --- RELATIONS ---
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="sent_messages", on_delete=models.CASCADE
    )
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="received_messages", on_delete=models.CASCADE
    )

    # --- CONTENT ---
    message_type = models.CharField(max_length=10, choices=MsgType.choices, default=MsgType.TEXT)
    content = models.TextField(blank=True, null=True)
    
    # render_cache: Stores calculated layout data (e.g., aspect ratios) for UI optimization
    render_cache = models.JSONField(default=dict, blank=True)

    # --- 1. REPLY FEATURE (Snapshot Strategy) ---
    # The hard link to the parent message (for "Jump to Message")
    reply_to = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, # If parent deleted, keep the reply but break the link
        null=True, 
        blank=True, 
        related_name='replies'
    )
    # The SNAPSHOT: Stores "Alice: Hello..." so we can render the UI 
    # without querying the parent message table (High Performance)
    # Structure: { "id": 123, "sender_name": "Alice", "preview": "Hello...", "media_type": "image" }
    reply_metadata = models.JSONField(null=True, blank=True)

    # --- 2. FORWARD FEATURE ---
    is_forwarded = models.BooleanField(default=False)
    # Optional: Display "Forwarded from Bob" without linking to Bob's ID (Privacy)
    forward_source_name = models.CharField(max_length=255, null=True, blank=True)

    # --- 3. EDIT FEATURE ---
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)

    # --- METADATA ---
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["sender", "receiver", "-created_at"]), # Fast history lookup
        ]

    def __str__(self):
        return f"{self.sender_id}->{self.receiver_id} | {self.message_type} | {self.status}"






class MediaAsset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE  = "file",  "File"

    # Link to ChatMessage (many assets per message)
    message = models.ForeignKey(
        "ChatMessage",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="media_assets"
    )

    # Storage identity
    bucket = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024, unique=True, db_index=True)

    # What it is
    kind = models.CharField(max_length=10, choices=Kind.choices)
    content_type = models.CharField(max_length=255, blank=True, null=True)

    # Original file facts
    file_name = models.CharField(max_length=255, blank=True, null=True)
    file_size = models.BigIntegerField(blank=True, null=True)

    # Optional media info
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    duration_seconds = models.FloatField(blank=True, null=True)

    # Processing lifecycle
    processing_status = models.CharField(
        max_length=16,
        default="queued",
        choices=[
            ("queued", "Queued"),
            ("running", "Running"),
            ("done", "Done"),
            ("partial", "Partial"),
            ("failed", "Failed"),
        ],
        db_index=True,
    )
    processing_progress = models.FloatField(default=0.0)

    # Variants & URLs
    variants = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} | {self.file_name or self.object_key}"
    
    
    @property
    def url(self):
        # FIX: Remove the status check. 
        # Always allow access to the current object_key (whether raw or optimized).
        if not self.object_key:
            return None
        
        from utils.aws import s3, AWS_BUCKET
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": self.object_key},
            ExpiresIn=3600
        )
        
    @property
    def thumbnail_url(self):
        from utils.aws import s3, AWS_BUCKET
    
        # 1. Try to get the specific thumbnail variant
        thumb_key = self.variants.get("thumbnail")

        if thumb_key:
            return s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": thumb_key},
                ExpiresIn=3600
            )

        # 2. Fallback: If no thumb exists (e.g. processing pending/failed),
        # return the main URL so the UI shows the full-size image.
        if self.kind == "image":
            return self.url
            
        return None