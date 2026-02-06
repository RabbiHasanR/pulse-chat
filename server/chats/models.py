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
    
    


from django.db import models
from django.conf import settings

# --- 1. CONVERSATION (The "Smart" Container) ---
class Conversation(models.Model):
    """
    The 'Container' for messages between two users.
    
    SCALABILITY STRATEGY (Stage 1 - Denormalization):
    Instead of querying the huge 'ChatMessage' table to build the Chat List,
    we store the 'Last Message' and 'Unread Counts' directly here.
    This makes loading the Chat List O(1) instead of O(N).
    """
    participant_1 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conv_p1")
    participant_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conv_p2")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    # --- DENORMALIZED FIELDS (Updated on every Send/Read) ---
    
    # Snapshot of the last message
    last_message_content = models.TextField(null=True, blank=True)
    last_message_type = models.CharField(max_length=10, default='text') # 'text', 'image', 'video', etc.
    last_message_time = models.DateTimeField(null=True, blank=True)
    
    # JSON Counter: { "user_id_string": count }
    # Example: { "101": 0, "102": 5 } -> User 102 has 5 unread messages.
    unread_counts = models.JSONField(default=dict, blank=True)

    class Meta:
        # Ensure only one conversation exists between two specific users
        # Note: You must enforce sorting (p1 < p2) in your Service logic when creating.
        unique_together = ('participant_1', 'participant_2')
        indexes = [
            # Compound indexes for blazing fast sorting of "My Chats"
            models.Index(fields=['participant_1', '-updated_at']),
            models.Index(fields=['participant_2', '-updated_at']),
        ]

    def __str__(self):
        return f"Chat: {self.participant_1_id} <-> {self.participant_2_id}"


# --- 2. CHAT MESSAGE ---
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
        FAILED    = "failed",    "Failed"

    # Link to Conversation (Makes history queries efficient)
    conversation = models.ForeignKey(
        Conversation, related_name="messages", on_delete=models.CASCADE, null=True, db_index=True
    )
    
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="sent_messages", on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="received_messages", on_delete=models.CASCADE)

    message_type = models.CharField(max_length=10, choices=MsgType.choices, default=MsgType.TEXT)
    content = models.TextField(blank=True, null=True)
    
    # Optimization: Store count here to avoid JOINs on MediaAsset table for list views
    asset_count = models.IntegerField(default=0) 

    # UI Optimization: Pre-calculated layout data (aspect ratios, etc.)
    render_cache = models.JSONField(default=dict, blank=True)
    
    # --- FEATURES ---
    
    # Reply: Soft link to parent (for DB constraints)
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    # Reply: Hard snapshot (for fast rendering without querying parent)
    reply_metadata = models.JSONField(null=True, blank=True)
    
    is_forwarded = models.BooleanField(default=False)
    forward_source_name = models.CharField(max_length=255, null=True, blank=True)
    
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
            # The most common query: "Get messages for this conversation, newest first"
            models.Index(fields=["conversation", "-created_at"]), 
        ]

    def __str__(self):
        return f"Msg {self.id} ({self.status})"


# --- 3. MEDIA ASSET (Public URLs) ---
class MediaAsset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE  = "file",  "File"

    message = models.ForeignKey(ChatMessage, null=True, blank=True, on_delete=models.CASCADE, related_name="media_assets")
    
    # S3 / Storage Info
    bucket = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024, db_index=True)
    
    kind = models.CharField(max_length=10, choices=Kind.choices)
    content_type = models.CharField(max_length=255, blank=True, null=True)

    # Metadata for UI rendering
    file_name = models.CharField(max_length=255, blank=True, null=True)
    file_size = models.BigIntegerField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    duration_seconds = models.FloatField(blank=True, null=True)
    
    # Stores keys for generated variants (thumbnail, 480p, etc.)
    variants = models.JSONField(default=dict, blank=True)
    
    processing_status = models.CharField(
        max_length=16,
        default="queued",
        choices=[("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")],
        db_index=True,
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def url(self):
        """
        Returns the Public S3 URL.
        Browser caches this indefinitely (Performance Win).
        """
        if not self.bucket or not self.object_key:
            return None
        return f"https://{self.bucket}.s3.amazonaws.com/{self.object_key}"

    @property
    def thumbnail_url(self):
        """
        Returns the Public Thumbnail URL if available, else falls back to main URL.
        """
        thumb_key = self.variants.get("thumbnail")
        if thumb_key:
            return f"https://{self.bucket}.s3.amazonaws.com/{thumb_key}"
        
        # Fallback: If it's an image but thumbnail processing failed or is pending,
        # return the full image so the UI isn't broken.
        if self.kind == self.Kind.IMAGE:
            return self.url
            
        return None


