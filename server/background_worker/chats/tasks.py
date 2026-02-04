import asyncio
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db.models import Max

from botocore.exceptions import BotoCoreError, ClientError
from socket import timeout as SocketTimeout
from celery.exceptions import SoftTimeLimitExceeded, MaxRetriesExceededError

# --- IMPORTS FROM YOUR REDIS MODULE ---
# We use sync_redis_client for Celery to avoid Event Loop crashes
from utils.redis_client import sync_redis_client, RedisKeys 

from chats.models import ChatMessage, MediaAsset

# Processors
from utils.media_processors.image import ImageProcessor
from utils.media_processors.video import VideoProcessor
from utils.media_processors.audio import AudioProcessor
from utils.media_processors.file import FileProcessor

# Helper to generate room name
def room(user_id):
    return f"user_{user_id}"

# ----------------------------------------------------------------------------
# HELPER: DIRECT SOCKET PUSH (Bypasses Celery Queue)
# ----------------------------------------------------------------------------
def _send_socket_update_directly(user_id, payload):
    """
    Optimized helper for high-frequency updates (like Video Progress).
    Sends directly to Channel Layer, skipping the overhead of queuing a new Celery task.
    """
    try:
        channel_layer = get_channel_layer()
        # async_to_sync is safe here because we are interacting with Channels, not Redis directly
        async_to_sync(channel_layer.group_send)(room(user_id), {
            "type": "forward_event",
            "payload": payload,
        })
    except Exception as e:
        print(f"Direct Socket Push Failed: {e}")

# ----------------------------------------------------------------------------
# 1. NOTIFICATION TASK (Reliable - Default Queue)
# ----------------------------------------------------------------------------
@shared_task(
    queue='default',
    acks_late=True,             
    reject_on_worker_lost=True, 
    retry_backoff=True,         
    max_retries=3,              
    time_limit=10,              
    expires=60,                
)
def notify_message_event(payload: dict):
    """
    Handles critical real-time events (New Message, Status Change).
    Uses Synchronous Redis to prevent Event Loop conflicts.
    """
    data = payload.get("data", {})
    message_id = data.get("message_id")
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    
    status = data.get("status", "pending")
    processing_status = data.get("processing_status", "queued")

    if not message_id or not sender_id or not receiver_id:
        return

    channel_layer = get_channel_layer()

    # 1. Broadcast to Sender (Echo for multi-device sync) & Receiver
    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # 2. Presence / Seen Logic
    # We only check this if the message is ready to be seen (Text or Processed Media)
    is_media_ready = (status != "pending") and (processing_status == "done")
    is_text_message = (data.get("message_type") == "text")
    should_check_seen = is_media_ready or is_text_message

    if should_check_seen:
        # A. Check if Receiver is Online (Using SYNC Redis)
        is_online = sync_redis_client.sismember(RedisKeys.ONLINE_USERS, receiver_id)

        if is_online:
            # B. Check if Receiver is Viewing (Using SYNC Redis)
            viewing_key = RedisKeys.viewing(receiver_id, sender_id)
            is_viewing = sync_redis_client.scard(viewing_key) > 0

            if is_viewing:
                # SCENARIO: User is looking at the chat -> Mark SEEN
                # Use .update() for atomicity
                ChatMessage.objects.filter(id=message_id).update(status="seen")
                
                # Notify Sender: "Blue Ticks"
                read_receipt = {
                    "type": "chat_read_receipt",
                    "data": {
                        "message_id": message_id,
                        "conversation_id": data.get("conversation"), 
                        "reader_id": receiver_id,
                        "last_read_id": message_id
                    }
                }
                async_to_sync(channel_layer.group_send)(room(sender_id), {
                    "type": "forward_event",
                    "payload": read_receipt,
                })

# ----------------------------------------------------------------------------
# 2. MARK DELIVERED TASK (On Connect)
# ----------------------------------------------------------------------------
@shared_task(ignore_result=True, time_limit=10, expires=60)
def mark_delivered_and_notify_senders(user_id):
    """
    Runs when a user comes online.
    Marks all 'SENT' messages as 'DELIVERED' and notifies senders.
    """
    # 1. Aggregation: Find who sent messages to this user that are still just 'SENT'
    pending_groups = ChatMessage.objects.filter(
        receiver_id=user_id,
        status=ChatMessage.Status.SENT
    ).values('sender_id').annotate(last_id=Max('id'))

    if not pending_groups:
        return

    # 2. Bulk Database Update
    ChatMessage.objects.filter(
        receiver_id=user_id,
        status=ChatMessage.Status.SENT
    ).update(status=ChatMessage.Status.DELIVERED)

    # 3. Parallel Notifications (Asyncio Wrapper)
    async def send_parallel_notifications():
        channel_layer = get_channel_layer()
        tasks = []

        for entry in pending_groups:
            sender_id = entry['sender_id']
            last_msg_id = entry['last_id']
            
            event = {
                "type": "forward_event",
                "payload": {
                    "type": "chat_delivery_receipt",
                    "data": {
                        "receiver_id": user_id,
                        "last_delivered_id": last_msg_id
                    }
                }
            }
            tasks.append(channel_layer.group_send(f"user_{sender_id}", event))
        
        if tasks:
            await asyncio.gather(*tasks)

    # Run the async inner function synchronously
    async_to_sync(send_parallel_notifications)()

# ----------------------------------------------------------------------------
# HELPER: FINALIZE ASSET
# ----------------------------------------------------------------------------
def _finalize_asset(asset_id, result_data):
    """
    Production-Ready Finalizer:
    1. Updates MediaAsset metadata.
    2. Checks Real-time Status (Seen/Delivered) via Redis.
    3. Performs ONE atomic DB update for both Asset and Message.
    4. Pushes WebSocket event DIRECTLY (No extra task).
    """
    try:
        # 1. Fetch Data (Single Query)
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        receiver_id = msg.receiver_id
        sender_id = msg.sender_id

        # 2. Update Asset Metadata (InMemory)
        if result_data:
            asset.object_key = result_data.get("object_key", asset.object_key)
            if "width" in result_data: asset.width = result_data["width"]
            if "height" in result_data: asset.height = result_data["height"]
            if "file_size" in result_data: asset.file_size = result_data["file_size"]
            existing_vars = asset.variants or {}
            existing_vars.update(result_data.get("variants", {}))
            asset.variants = existing_vars
        
        asset.processing_status = "done"
        asset.processing_progress = 100.0

        # 3. Determine New Message Status (Redis Check)
        # We assume the message starts as SENT/DELIVERED. 
        # We only want to upgrade it to SEEN if the user is looking RIGHT NOW.
        new_status = msg.status # Default: Keep existing status
        
        # Check if Receiver is Viewing this chat
        viewing_key = RedisKeys.viewing(receiver_id, sender_id)
        is_viewing = sync_redis_client.scard(viewing_key) > 0
        
        if is_viewing:
            new_status = 'seen'
        elif msg.status == 'sent': 
            # If it was just 'sent', check if they are at least online now to mark 'delivered'
            is_online = sync_redis_client.sismember(RedisKeys.ONLINE_USERS, receiver_id)
            if is_online:
                new_status = 'delivered'

        # 4. ATOMIC COMMIT (The Scalability Win)
        with transaction.atomic():
            asset.save()
            # Only update message status if it changed (optimization)
            if new_status != msg.status:
                msg.status = new_status
                msg.save(update_fields=['status', 'updated_at'])

        # 5. Direct WebSocket Broadcast (Fastest)
        # No need to queue another Celery task. Redis Publish is fast (<2ms).
        channel_layer = get_channel_layer()
        
        payload = {
            "type": "chat_message_update",
            "success": True,
            "data": {
                "message_id": msg.id,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "status": new_status, # <--- Sends the LATEST status
                "processing_status": "done",
                "media_url": asset.url,
                "thumbnail_url": asset.thumbnail_url,
                "width": asset.width,
                "height": asset.height,
            }
        }

        # Send to Receiver
        async_to_sync(channel_layer.group_send)(f"user_{receiver_id}", {
            "type": "forward_event", 
            "payload": payload
        })
        
        # Send to Sender (Sync other devices)
        async_to_sync(channel_layer.group_send)(f"user_{sender_id}", {
            "type": "forward_event", 
            "payload": payload
        })

        # 6. Optional: If 'seen', send a separate Read Receipt event
        # This ensures the Sender's UI updates the ticks specifically
        if new_status == 'seen' and msg.status != 'seen':
             read_receipt = {
                "type": "chat_read_receipt",
                "data": {
                    "message_id": msg.id,
                    "reader_id": receiver_id,
                    "last_read_id": msg.id
                }
            }
             async_to_sync(channel_layer.group_send)(f"user_{sender_id}", {
                "type": "forward_event", 
                "payload": read_receipt
            })

    except Exception as e:
        print(f"Finalize Failed for {asset_id}: {e}")
        # Log error to Sentry/CloudWatch

# ----------------------------------------------------------------------------
# 3. MEDIA TASKS (Optimized)
# ----------------------------------------------------------------------------

@shared_task(
    bind=True, 
    queue='video_queue',
    acks_late=True,
    reject_on_worker_lost=False,
    soft_time_limit=3600,
    time_limit=3660,
    max_retries=3
)
def process_video_task(self, asset_id):
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        progress_key = f"asset_progress:{asset_id}"
        last_ws_progress = 0

        # --- CALLBACKS ---
        def on_progress(percent, thumb_key=None):
            nonlocal last_ws_progress
            cache.set(progress_key, percent, timeout=3600)

            # A. Thumbnail Update (Important - Use Celery or DB Update directly)
            if thumb_key:
                asset.variants['thumbnail'] = thumb_key
                MediaAsset.objects.filter(id=asset.id).update(variants=asset.variants)
                
                # Notify Frontend (Can use direct push for speed)
                _send_socket_update_directly(msg.sender_id, {
                    "type": "chat_message_update", 
                    "data": {
                        "message_id": msg.id,
                        "thumbnail_url": asset.thumbnail_url,
                        "processing_status": "running",
                        "stage": "thumbnail_ready",
                        "progress": round(percent, 1)
                    }
                })

            # B. Progress Bar (Throttled & Direct)
            if abs(percent - last_ws_progress) >= 2:
                last_ws_progress = percent
                # OPTIMIZATION: Send DIRECTLY to socket. Do not spawn a Celery task.
                # This saves the Redis/Celery queue from getting flooded.
                payload = {
                    "type": "chat_message_update", 
                    "data": {
                        "message_id": msg.id,
                        "processing_status": "running",
                        "progress": round(percent, 1)
                    }
                }
                _send_socket_update_directly(msg.sender_id, payload)
                _send_socket_update_directly(msg.receiver_id, payload)

        def on_checkpoint(variant_name):
            # Atomic update to prevent race conditions
            current_asset = MediaAsset.objects.only('variants').get(id=asset_id)
            current_vars = current_asset.variants or {}
            if 'hls_parts' not in current_vars: current_vars['hls_parts'] = {}
            current_vars['hls_parts'][variant_name] = True
            
            MediaAsset.objects.filter(id=asset_id).update(variants=current_vars)
            asset.variants = current_vars 

        def on_playable(master_key):
            MediaAsset.objects.filter(id=asset_id).update(object_key=master_key)
            asset.object_key = master_key
            
            # Notify Playable state
            payload = {
                "type": "chat_message_update", 
                "data": {
                    "message_id": msg.id,
                    "video_url": asset.url, 
                    "processing_status": "running", 
                    "stage": "playable",
                    "progress": round(last_ws_progress, 1)
                }
            }
            notify_message_event.delay(payload) # Important state change -> Use Queue

        # --- EXECUTION ---
        MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        processor = VideoProcessor(asset)
        master_key, thumb_key = processor.process(
            on_progress_callback=on_progress,
            on_checkpoint_save=on_checkpoint,
            on_playable_callback=on_playable
        )
        
        result_data = {
            "object_key": master_key, 
            "variants": {
                "type": "hls", "master": master_key, "thumbnail": thumb_key,
                "hls_parts": asset.variants.get('hls_parts', {})
            }
        }
        _finalize_asset(asset_id, result_data)
        cache.delete(progress_key)

    except (BotoCoreError, ClientError, SocketTimeout, ConnectionError) as e:
        try:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
            _handle_failure(asset_id, f"Max retries exceeded: {e}")
    except SoftTimeLimitExceeded:
        _handle_failure(asset_id, "Time limit exceeded")
    except Exception as e:
        _handle_failure(asset_id, e)

@shared_task(
    bind=True, 
    queue='image_queue', 
    acks_late=True,
    reject_on_worker_lost=False,
    soft_time_limit=60,
    time_limit=70,
    max_retries=3
)
def process_image_task(self, asset_id):
    try:
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0: return
        asset = MediaAsset.objects.get(id=asset_id)
        processor = ImageProcessor(asset)
        result_data = processor.process()
        _finalize_asset(asset_id, result_data)
    except (BotoCoreError, ClientError, SocketTimeout, ConnectionError) as e:
        try:
             raise self.retry(exc=e, countdown=5 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
             _handle_failure(asset_id, f"Max retries exceeded: {e}")
    except SoftTimeLimitExceeded:
        _handle_failure(asset_id, "Time limit exceeded")
    except Exception as e:
        _handle_failure(asset_id, e)

@shared_task(
    bind=True, 
    queue='audio_queue',
    acks_late=True,
    reject_on_worker_lost=False,
    soft_time_limit=900,
    time_limit=930,
    max_retries=3
)
def process_audio_task(self, asset_id):
    try:
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0: return
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # Initial 'Running' Notification
        notify_message_event.delay({
            "type": "chat_message_update", 
            "data": {"message_id": msg.id, "asset_id": asset.id, "processing_status": "running", "stage": "processing"}
        })
        
        processor = AudioProcessor(asset)
        result_data = processor.process()
        _finalize_asset(asset_id, result_data)
    except (BotoCoreError, ClientError, ConnectionError) as e:
        try:
             raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except Exception:
             _handle_failure(asset_id, e)
    except Exception as e:
        _handle_failure(asset_id, e)

@shared_task(
    bind=True, 
    queue='file_queue',
    acks_late=True,
    reject_on_worker_lost=False,
    soft_time_limit=300,
    time_limit=310,
    max_retries=3
)
def process_file_task(self, asset_id):
    try:
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0: return
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        notify_message_event.delay({
            "type": "chat_message_update", 
            "data": {"message_id": msg.id, "asset_id": asset.id, "processing_status": "running", "stage": "processing"}
        })
        
        processor = FileProcessor(asset)
        result_data = processor.process()
        _finalize_asset(asset_id, result_data)
    except (BotoCoreError, ClientError, ConnectionError) as e:
        try:
             raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except Exception:
             _handle_failure(asset_id, e)
    except Exception as e:
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# FAILURE HANDLER
# ----------------------------------------------------------------------------
def _handle_failure(asset_id, error):
    print(f"Processing Failed for Asset {asset_id}: {error}")
    try:
        # 1. Fetch Asset & Message
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # 2. Check Partial Success (e.g. Video playable but thumbnail failed)
        hls_parts = asset.variants.get('hls_parts', {})
        is_playable = bool(hls_parts)
        
        new_status = "partial" if is_playable else "failed"
        if is_playable:
            asset.variants['error_log'] = str(error)

        # 3. Update DB
        asset.processing_status = new_status
        asset.save(update_fields=["processing_status", "variants"])
        
        # 4. Direct WebSocket Notification (OPTIMIZED)
        # Bypasses the Celery queue. Fast & Consistent.
        channel_layer = get_channel_layer()
        
        payload = {
            "type": "chat_message_update",
            "success": False,
            "data": {
                "message_id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "status": msg.status, # Keep existing status (e.g. 'sent')
                "processing_status": new_status,
                "stage": "failed",
                "error": str(error),
                "media_url": asset.url if is_playable else None
            }
        }

        # Send to Receiver
        async_to_sync(channel_layer.group_send)(f"user_{msg.receiver_id}", {
            "type": "forward_event", 
            "payload": payload
        })
        
        # Send to Sender
        async_to_sync(channel_layer.group_send)(f"user_{msg.sender_id}", {
            "type": "forward_event", 
            "payload": payload
        })
        
    except Exception as e:
        print(f"Critical DB Error in Failure Handler: {e}")
        
        
        
        
        
        



@shared_task(
    bind=True,
    queue='default',       # <--- USE DEFAULT QUEUE (Isolate from heavy media tasks)
    acks_late=True,        # Ensure task isn't lost if worker crashes mid-execution
    soft_time_limit=60,    # Should finish fast. If not, stop it.
    time_limit=120,        # Hard kill after 2 mins
    max_retries=3
)
def cleanup_stuck_assets(self):
    """
    Periodic Task: Finds assets stuck in 'queued' (Abandoned) or 'running' (Crashed)
    and marks them as failed. Updates the parent Message status if necessary.
    """
    try:
        now = timezone.now()
        
        # 1. Define Thresholds
        # Abandoned: User got URL but never uploaded (24 hours grace period)
        abandoned_threshold = now - timedelta(hours=24)
        # Crashed: Worker started but died/timed out (2 hours is generous for any file)
        crash_threshold = now - timedelta(hours=2)

        # 2. Find Zombies (Stuck Assets)
        # We assume any asset older than these limits is dead.
        stuck_assets = MediaAsset.objects.filter(
            Q(processing_status='queued', created_at__lte=abandoned_threshold) |
            Q(processing_status='running', created_at__lte=crash_threshold)
        ).select_related('message')

        if not stuck_assets.exists():
            return "No stuck assets found."

        # 3. Collect Message IDs first (Need this before we update the assets)
        message_ids = set(stuck_assets.values_list('message_id', flat=True))

        print(f"🧹 Cleanup: Found {stuck_assets.count()} stuck assets across {len(message_ids)} messages.")

        # 4. Mark Assets as Failed (Bulk Update)
        # This is efficient (1 DB query)
        count = stuck_assets.update(
            processing_status='failed',
            variants={'error_log': 'Cleanup: Upload timed out (Abandoned) or Worker Crashed'}
        )

        # 5. Check Consistency for Affected Messages
        # Fetch fresh data to decide if the message is dead or partial
        affected_messages = ChatMessage.objects.filter(id__in=message_ids).annotate(
            valid_assets=Count('media_assets', filter=Q(media_assets__processing_status='done')),
            failed_assets=Count('media_assets', filter=Q(media_assets__processing_status='failed'))
        )

        channel_layer = get_channel_layer()

        # 6. Iterate and Notify (Safely)
        updated_msgs = 0
        for msg in affected_messages:
            try:
                # LOGIC: Is the message completely dead?
                # Dead = No Text Content AND No Valid (Done) Assets
                is_dead = (not msg.content) and (msg.valid_assets == 0)

                if is_dead:
                    # Mark Message as FAILED
                    msg.status = ChatMessage.Status.FAILED
                    msg.save(update_fields=['status'])
                else:
                    # Message is partially valid (e.g., has text or 1 success out of 3 images)
                    # If it was stuck in 'pending', free it to 'sent' so the valid parts are visible
                    if msg.status == ChatMessage.Status.PENDING:
                        msg.status = ChatMessage.Status.SENT
                        msg.save(update_fields=['status'])

                # Notify Frontend (Update UI)
                payload = {
                    "type": "chat_message_update",
                    "data": {
                        "message_id": msg.id,
                        "status": msg.status,           # 'failed' or 'sent'
                        "processing_status": "failed",  # Tells UI to stop spinner
                        "sender_id": msg.sender_id,
                        "receiver_id": msg.receiver_id,
                    }
                }
                
                # Broadcast to Sender (so they can retry)
                async_to_sync(channel_layer.group_send)(f"user_{msg.sender_id}", {
                    "type": "forward_event", "payload": payload
                })
                
                # Broadcast to Receiver (so they stop seeing the spinner)
                async_to_sync(channel_layer.group_send)(f"user_{msg.receiver_id}", {
                    "type": "forward_event", "payload": payload
                })
                updated_msgs += 1

            except Exception as e:
                # If notifying for ONE message fails, log it but CONTINUE the loop.
                # Do not let one bad socket connection break the cleanup for everyone else.
                print(f"⚠️ Failed to notify cleanup for msg {msg.id}: {e}")
                continue

        return f"Cleaned {count} assets. Updated {updated_msgs} messages."

    except Exception as e:
        # Catch Critical DB Connection Errors
        print(f"❌ Critical Cleanup Failure: {e}")
        # Retry logic for DB locks
        raise self.retry(exc=e, countdown=60)