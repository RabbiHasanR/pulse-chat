import asyncio
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db.models import Max

from botocore.exceptions import BotoCoreError, ClientError
from socket import timeout as SocketTimeout
from celery.exceptions import SoftTimeLimitExceeded, MaxRetriesExceededError

from utils.redis_client import redis_client
from utils.realtime import (
    room,
    is_user_viewing_me,
    notify_single_status,
    send_unread_summary,
)
from chats.models import ChatMessage, MediaAsset

# Processors
from utils.media_processors.image import ImageProcessor
from utils.media_processors.video import VideoProcessor
from utils.media_processors.audio import AudioProcessor
from utils.media_processors.file import FileProcessor

# ----------------------------------------------------------------------------
# 1. NOTIFICATION TASK (Lightweight - Default Queue)
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
    Handles real-time WebSocket events and 'Seen' status logic.
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

    # 1. Forward Event to Sender & Receiver WebSockets
    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # 2. Presence / Seen Logic
    is_media_ready = (status != "pending") and (processing_status == "done")
    is_text_message = (data.get("message_type") == "text")
    should_check_seen = is_media_ready or is_text_message

    if should_check_seen:
        if async_to_sync(redis_client.sismember)("online_users", receiver_id):
            if async_to_sync(is_user_viewing_me)(receiver_id=receiver_id, sender_id=sender_id):
                ChatMessage.objects.filter(id=message_id).update(status="seen")
                async_to_sync(notify_single_status)(
                    message_id=message_id,
                    receiver_id=receiver_id,
                    sender_id=sender_id,
                    status="seen",
                )
            else:
                async_to_sync(send_unread_summary)(
                    to_user_id=receiver_id,
                    from_user_id=sender_id,
                )
        else:
            async_to_sync(send_unread_summary)(
                to_user_id=receiver_id,
                from_user_id=sender_id,
            )

# ----------------------------------------------------------------------------
# HELPER: FINALIZE ASSET (Avoids Duplication)
# ----------------------------------------------------------------------------
def _finalize_asset(asset_id, result_data):
    """
    Common logic to mark asset as DONE, update DB, and notify Frontend.
    Optimized payload but includes essential routing IDs.
    """
    try:
        # 1. Database Updates
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

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
        asset.save()

        if msg.status == 'pending':
            msg.status = 'sent'
            msg.save(update_fields=["status", "updated_at"])

        # 2. Optimized Notification Payload
        payload = {
            "type": "chat_message_update",
            "success": True,
            "data": {
                # ROUTING & IDENTITY
                "message_id": msg.id,
                "sender_id": msg.sender_id,     # <--- Added back
                "receiver_id": msg.receiver_id, # <--- Added back
                
                # STATUS CHANGE
                "status": "sent",
                "processing_status": "done",
                "stage": "done",
                "progress": 100.0,
                
                # NEW VISUAL DATA
                "media_url": asset.url,
                "thumbnail_url": asset.thumbnail_url,
                "width": asset.width,
                "height": asset.height,
            }
        }
        notify_message_event.delay(payload)
    
    except Exception as e:
        print(f"Finalize Failed for {asset_id}: {e}")

# ----------------------------------------------------------------------------
# 2. VIDEO PROCESSING TASK (Heavy - Video Queue)
# ----------------------------------------------------------------------------
@shared_task(
    bind=True, 
    queue='video_queue',
    acks_late=True,              # Retry if worker crashes/loses power (Persistence)
    reject_on_worker_lost=False, # SAFETY: Do NOT retry if FFmpeg crashes the process (prevents Poison Pill loop)
    soft_time_limit=3600,        # Raise exception after 1 hour (allows cleanup)
    time_limit=3660,             # Hard kill after 1h 1m
    max_retries=3                # Allow 3 retries, but ONLY for network/infrastructure errors
)
def process_video_task(self, asset_id):
    try:
        # Optimization: Fetch message details in single query
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # Redis Key for ephemeral progress
        progress_key = f"asset_progress:{asset_id}"
        last_ws_progress = 0

        # --- HANDLER 1: PROGRESS (Optimized Throttling) ---
        def on_progress(percent, thumb_key=None):
            nonlocal last_ws_progress
            
            # A. Update Redis (Lightweight & Fast)
            cache.set(progress_key, percent, timeout=3600)

            # B. Thumbnail Ready (Happens once)
            if thumb_key:
                # Direct DB update (faster than .save())
                asset.variants['thumbnail'] = thumb_key
                MediaAsset.objects.filter(id=asset.id).update(variants=asset.variants)
                
                notify_message_event.delay({
                    "type": "chat_message_update", 
                    "data": {
                        "message_id": msg.id,
                        "thumbnail_url": asset.thumbnail_url,
                        "processing_status": "running",
                        "stage": "thumbnail_ready",
                        "progress": round(percent, 1)
                    }
                })

            # C. WebSocket Throttle (Only send if changed > 2%)
            if abs(percent - last_ws_progress) >= 2:
                last_ws_progress = percent
                notify_message_event.delay({
                    "type": "chat_message_update", 
                    "data": {
                        "message_id": msg.id,
                        "processing_status": "running",
                        "progress": round(percent, 1)
                    }
                })

        # --- HANDLER 2: CHECKPOINT (Resume Logic) ---
        def on_checkpoint(variant_name):
            # Fetch only the 'variants' field to save bandwidth
            current_asset = MediaAsset.objects.only('variants').get(id=asset_id)
            current_vars = current_asset.variants or {}
            
            if 'hls_parts' not in current_vars:
                current_vars['hls_parts'] = {}
            
            current_vars['hls_parts'][variant_name] = True
            
            # Atomic-style update
            MediaAsset.objects.filter(id=asset_id).update(variants=current_vars)
            
            # Update local reference for subsequent logic
            asset.variants = current_vars 
            print(f"Checkpoint saved: {variant_name}")

        # --- HANDLER 3: PLAYABLE (Progressive Availability) ---
        def on_playable(master_key):
            # 1. Update DB immediately so the URL is valid
            MediaAsset.objects.filter(id=asset_id).update(object_key=master_key)
            asset.object_key = master_key
            
            # 2. Notify Frontend to show "Play" button
            notify_message_event.delay({
                "type": "chat_message_update", 
                "data": {
                    "message_id": msg.id,
                    "video_url": asset.url, 
                    "processing_status": "running", 
                    "stage": "playable", # <--- Triggers Play Button in UI
                    "progress": round(last_ws_progress, 1)
                }
            })
            print(f"Video is playable: {master_key}")

        # --- START EXECUTION ---
        MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        
        processor = VideoProcessor(asset)
        master_key, thumb_key = processor.process(
            on_progress_callback=on_progress,
            on_checkpoint_save=on_checkpoint,
            on_playable_callback=on_playable
        )
        
        # Prepare final result
        result_data = {
            "object_key": master_key, 
            "variants": {
                "type": "hls", 
                "master": master_key, 
                "thumbnail": thumb_key,
                "hls_parts": asset.variants.get('hls_parts', {})
            }
        }
        
        _finalize_asset(asset_id, result_data)
        cache.delete(progress_key)

    # --- EXCEPTION HANDLING: SMART RETRIES ---

    # 1. Infrastructure Errors -> RETRY
    except (BotoCoreError, ClientError, SocketTimeout, ConnectionError) as e:
        print(f"Infrastructure Error for {asset_id}: {e}. Retrying...")
        try:
            # Exponential Backoff: Wait 10s, 20s, 40s...
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
            _handle_failure(asset_id, f"Max retries exceeded for infra error: {e}")

    # 2. Timeout -> FAIL GRACEFULLY
    except SoftTimeLimitExceeded:
        _handle_failure(asset_id, "Processing timed out (Soft limit exceeded)")
    
    # 3. Logic/FFmpeg Errors -> FAIL IMMEDIATELY (Do not retry bad files)
    except Exception as e:
        print(f"Logic/FFmpeg Error for {asset_id}: {e}. Failing immediately.")
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 3. IMAGE PROCESSING TASK (Medium - Image Queue)
# ----------------------------------------------------------------------------
@shared_task(
    bind=True, 
    queue='image_queue', 
    acks_late=True,              # Retry if worker loses power
    reject_on_worker_lost=False, # SAFETY: Prevent crash loops on "Image Bomb" files
    soft_time_limit=60,          # Images must finish in 1 minute
    time_limit=70,               # Hard kill after 70s
    max_retries=3                # Retry network errors
)
def process_image_task(self, asset_id):
    try:
        # 1. Optimistic Update (Fast)
        # Update status immediately without fetching the full object first
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0:
            return # Asset was deleted

        # 2. Fetch Object
        asset = MediaAsset.objects.get(id=asset_id)

        # 3. Process
        processor = ImageProcessor(asset)
        result_data = processor.process()
        
        # 4. Finalize
        _finalize_asset(asset_id, result_data)

    # --- RETRY STRATEGY ---
    except (BotoCoreError, ClientError, SocketTimeout, ConnectionError) as e:
        # Network/S3 Glitch -> Retry
        try:
             # Wait 5s, 10s, 20s
             raise self.retry(exc=e, countdown=5 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
             _handle_failure(asset_id, f"Max retries exceeded: {e}")

    # --- TIMEOUT HANDLING ---
    except SoftTimeLimitExceeded:
        _handle_failure(asset_id, "Image processing timed out (file too large or complex)")

    # --- FAIL FAST ---
    except Exception as e:
        # Corrupt file / Pillow error -> Fail immediately
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 4. GENERIC FILE/AUDIO TASKS (Light - File/Audio Queue)
# ----------------------------------------------------------------------------
@shared_task(
    bind=True, 
    queue='audio_queue',          # Dedicated queue (keeps video workers free)
    acks_late=True,               # Resilience: If worker crashes, task is requeued
    reject_on_worker_lost=False,
    
    # TIMEOUT SETTINGS
    # 15 minutes allow for large uploads (e.g. 1-hour lectures/podcasts)
    # without cutting off the user prematurely.
    soft_time_limit=900,          # 15 min: Raises exception (Allows cleanup)
    time_limit=930,               # 15.5 min: Hard Kill (SIGKILL)
    
    max_retries=3                 # Retry network glitches
)
def process_audio_task(self, asset_id):
    try:
        # 1. Optimistic Status Update (DB)
        # Mark as running immediately so the user sees "Processing..."
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0: 
            return # Asset was deleted while queued

        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # 2. Notify Frontend (WebSocket)
        # Triggers the blue spinner on the client side
        notify_message_event.delay({
            "type": "chat_message_update", 
            "data": {
                "message_id": msg.id,
                "asset_id": asset.id,
                "processing_status": "running",
                "stage": "processing"
            }
        })

        # 3. Process (Validate -> Transcode -> Upload -> Cleanup)
        # The AudioProcessor handles "Zero-Disk" streaming and security checks internally
        processor = AudioProcessor(asset)
        result_data = processor.process()
        
        # 4. Success: Finalize
        # Saves the waveform JSON, updates status to 'done', and notifies 'playable'
        _finalize_asset(asset_id, result_data)

    # --- ERROR HANDLING STRATEGY ---

    except ValueError as e:
        # SECURITY / VALIDATION ERROR
        # Do NOT retry. The file is dangerous (virus) or corrupt.
        # The processor has already deleted the file from S3 to sanitize.
        _handle_failure(asset_id, e)

    except SoftTimeLimitExceeded:
        # TIMEOUT ERROR
        # Task took longer than 15 mins.
        _handle_failure(asset_id, Exception("Processing timed out (File too large)"))

    except (BotoCoreError, ClientError, ConnectionError) as e:
        # INFRASTRUCTURE ERROR
        # S3 is down or network blip. Retry with exponential backoff.
        try:
             # Retries in 10s, 20s, 40s
             raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except Exception:
             # Max retries hit, mark as failed
             _handle_failure(asset_id, e)

    except Exception as e:
        # GENERIC/LOGIC ERROR
        # Code bug or FFmpeg crash. Fail fast.
        _handle_failure(asset_id, e)

@shared_task(
    bind=True, 
    queue='file_queue',       # Separate queue (Files process very fast)
    acks_late=True,           # Resilience: If worker crashes, task is requeued
    reject_on_worker_lost=False,
    
    # TIMEOUT SETTINGS
    # 5 minutes is generous. PDF thumbnailing takes ~2-10 seconds.
    # We want to kill stuck processes early to free up workers.
    soft_time_limit=300,      
    time_limit=310,
    
    max_retries=3
)
def process_file_task(self, asset_id):
    try:
        # 1. Optimistic Status Update
        rows = MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        if rows == 0: return

        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # 2. Notify Frontend
        # Even though processing is fast, we notify "Processing" to show the spinner
        # instead of a static "Queued" state.
        notify_message_event.delay({
            "type": "chat_message_update", 
            "data": {
                "message_id": msg.id,
                "asset_id": asset.id,
                "processing_status": "running",
                "stage": "processing"
            }
        })

        # 3. Process
        # The FileProcessor decides internally whether to download (PDF) or just check headers (Zip/Doc)
        processor = FileProcessor(asset)
        result_data = processor.process()
        
        # 4. Success: Finalize
        # Updates DB with 'thumbnail' (if PDF) or just file metadata
        _finalize_asset(asset_id, result_data)

    # --- ERROR HANDLING ---

    except ValueError as e:
        # SECURITY ERROR (e.g., .exe disguised as .pdf)
        # The processor has already deleted the file from S3.
        # Fail fast, do not retry.
        _handle_failure(asset_id, e)

    except SoftTimeLimitExceeded:
        # TIMEOUT ERROR
        _handle_failure(asset_id, Exception("Processing timed out"))

    except (BotoCoreError, ClientError, ConnectionError) as e:
        # NETWORK ERROR
        # S3 is slow/down. Retry with backoff.
        try:
             raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except Exception:
             _handle_failure(asset_id, e)

    except Exception as e:
        # LOGIC ERROR
        # e.g., 'pdf2image' crashes on a weird PDF
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# FAILURE HANDLER
# ----------------------------------------------------------------------------
def _handle_failure(asset_id, error):
    print(f"Processing Failed for Asset {asset_id}: {error}")
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        
        # --- LOGIC UPDATE: CHECK FOR PARTIAL SUCCESS ---
        # Check if we have successfully processed at least one part
        hls_parts = asset.variants.get('hls_parts', {})
        is_playable = bool(hls_parts) # True if dictionary is not empty
        
        if is_playable:
            new_status = "partial"
            # Optional: Add a note about the error in variants for debugging
            asset.variants['error_log'] = str(error)
        else:
            new_status = "failed"
        # -----------------------------------------------

        asset.processing_status = new_status
        asset.save(update_fields=["processing_status", "variants"])
        
        msg = asset.message
        
        # Notification Payload
        payload = {
            "type": "chat_message_update", # Use update type so we don't confuse the frontend
            "success": False, # Still technically 'false' because it didn't finish 100%
            "data": {
                "message_id": msg.id,
                "status": "sent", # If partial, it is technically "sent" and viewable
                "processing_status": new_status,
                "stage": "failed", # UI can show an error icon
                "error": str(error),
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                
                # IMPORTANT: If partial, send the URL so the UI keeps the video player!
                "media_url": asset.url if is_playable else None
            }
        }
        notify_message_event.delay(payload)
        
    except Exception as e:
        print(f"Critical DB Error in Failure Handler: {e}")
        
        
        
        
        
        
        

@shared_task(ignore_result=True, time_limit=10, expires=60)
def mark_delivered_and_notify_senders(user_id):
    """
    Optimized:
    1. Single DB Query for IDs.
    2. Single DB Query for Update.
    3. Parallel Redis calls for Notifications.
    """
    # 1. Aggregation
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

    # 3. Parallel Notifications (The Scalable Part)
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
            # We assume channel_layer.group_send handles the sharding/connection pooling
            tasks.append(channel_layer.group_send(f"user_{sender_id}", event))
        
        if tasks:
            # Fire all requests concurrently. 
            # Redis handles 100k ops/sec, so 100 ops here is instant.
            await asyncio.gather(*tasks)

    async_to_sync(send_parallel_notifications)()