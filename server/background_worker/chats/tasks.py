import asyncio
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
    Updates DB state to 'done' and sends the final success notification.
    """
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # 1. Update Asset Metadata
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

        # 2. Update Message Status (Atomic Update)
        # Only update if it's still pending/sent to avoid reverting 'seen' status
        if msg.status == 'pending':
            ChatMessage.objects.filter(id=msg.id).update(status='sent', updated_at=msg.updated_at)

        # 3. Final Notification (Use Celery Queue for reliability)
        payload = {
            "type": "chat_message_update",
            "success": True,
            "data": {
                "message_id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "status": "sent",
                "processing_status": "done",
                "stage": "done",
                "progress": 100.0,
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
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        
        hls_parts = asset.variants.get('hls_parts', {})
        is_playable = bool(hls_parts)
        
        new_status = "partial" if is_playable else "failed"
        if is_playable:
            asset.variants['error_log'] = str(error)

        asset.processing_status = new_status
        asset.save(update_fields=["processing_status", "variants"])
        
        msg = asset.message
        
        payload = {
            "type": "chat_message_update",
            "success": False,
            "data": {
                "message_id": msg.id,
                "status": "sent",
                "processing_status": new_status,
                "stage": "failed",
                "error": str(error),
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "media_url": asset.url if is_playable else None
            }
        }
        notify_message_event.delay(payload)
        
    except Exception as e:
        print(f"Critical DB Error in Failure Handler: {e}")