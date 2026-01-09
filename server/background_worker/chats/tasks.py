from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
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
from utils.media_processors.video_processor import VideoProcessor

# ----------------------------------------------------------------------------
# 1. NOTIFICATION TASK (Lightweight - Default Queue)
# ----------------------------------------------------------------------------
@shared_task(queue='default')
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
    """
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # 1. Apply Processor Results
        if result_data:
            asset.object_key = result_data.get("object_key", asset.object_key)
            if "width" in result_data: asset.width = result_data["width"]
            if "height" in result_data: asset.height = result_data["height"]
            if "file_size" in result_data: asset.file_size = result_data["file_size"]
            
            # Merge variants
            existing_vars = asset.variants or {}
            existing_vars.update(result_data.get("variants", {}))
            asset.variants = existing_vars

        # 2. Mark Done
        asset.processing_status = "done"
        asset.processing_progress = 100.0
        asset.save()

        # 3. Update Message Status
        if msg.status == 'pending':
            msg.status = 'sent'
            msg.save(update_fields=["status", "updated_at"])

        # 4. Final Notification
        payload = {
            "type": "chat_message",
            "success": True,
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                "status": "sent",
                "processing_status": "done",
                "stage": "done",
                "progress": 100.0,
                "media_url": asset.url,
                "thumbnail_url": asset.thumbnail_url,
                "width": asset.width,
                "height": asset.height,
                "file_name": asset.file_name,
                "file_size": asset.file_size,
                "content_type": asset.content_type,
                "variants": asset.variants,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "created_at": str(msg.created_at),
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
    acks_late=True, 
    reject_on_worker_lost=True, 
    max_retries=3
)
def process_video_task(self, asset_id):
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # Redis Key for ephemeral progress (Optimization)
        progress_key = f"asset_progress:{asset_id}"
        last_ws_progress = 0

        # --- PROGRESS HANDLER ---
        def on_progress(percent, thumb_key=None):
            nonlocal last_ws_progress
            
            # 1. Update Redis (Fast)
            cache.set(progress_key, percent, timeout=3600)

            # 2. Immediate Thumbnail Update
            if thumb_key:
                asset.variants['thumbnail'] = thumb_key
                asset.save(update_fields=['variants'])
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

            # 3. WebSocket Throttle (Every 2%)
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

        # --- CHECKPOINT HANDLER ---
        def on_checkpoint(variant_name):
            asset.refresh_from_db()
            current_vars = asset.variants or {}
            if 'hls_parts' not in current_vars:
                current_vars['hls_parts'] = {}
            
            current_vars['hls_parts'][variant_name] = True
            asset.variants = current_vars
            asset.save(update_fields=['variants'])
            print(f"Checkpoint saved: {variant_name}")

        # Init Status
        asset.processing_status = "running"
        asset.save(update_fields=["processing_status"])

        # Execute Processor
        processor = VideoProcessor(asset)
        master_key, thumb_key = processor.process(
            on_progress_callback=on_progress,
            on_checkpoint_save=on_checkpoint
        )
        
        result_data = {
            "object_key": master_key, 
            "variants": {
                "type": "hls", 
                "master": master_key, 
                "thumbnail": thumb_key,
                "hls_parts": asset.variants.get('hls_parts', {})
            }
        }
        
        # Finalize
        _finalize_asset(asset_id, result_data)
        
        # Cleanup Redis
        cache.delete(progress_key)

    except Exception as e:
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 3. IMAGE PROCESSING TASK (Medium - Image Queue)
# ----------------------------------------------------------------------------
@shared_task(bind=True, queue='image_queue', acks_late=True)
def process_image_task(self, asset_id):
    try:
        asset = MediaAsset.objects.get(id=asset_id)
        asset.processing_status = "running"
        asset.save(update_fields=["processing_status"])

        processor = ImageProcessor(asset)
        result_data = processor.process()
        
        _finalize_asset(asset_id, result_data)

    except Exception as e:
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 4. GENERIC FILE/AUDIO TASKS (Light - File/Audio Queue)
# ----------------------------------------------------------------------------
@shared_task(bind=True, queue='audio_queue')
def process_audio_task(self, asset_id):
    # Future: Add AudioProcessor logic here
    _finalize_asset(asset_id, {})

@shared_task(bind=True, queue='file_queue')
def process_file_task(self, asset_id):
    # Files usually don't need processing, just mark done
    _finalize_asset(asset_id, {})

# ----------------------------------------------------------------------------
# FAILURE HANDLER
# ----------------------------------------------------------------------------
def _handle_failure(asset_id, error):
    print(f"Processing Failed for Asset {asset_id}: {error}")
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        asset.processing_status = "failed"
        asset.save(update_fields=["processing_status"])
        
        msg = asset.message
        payload = {
            "type": "chat_message",
            "success": False,
            "data": {
                "message_id": msg.id,
                "status": "pending",
                "processing_status": "failed",
                "stage": "failed",
                "error": str(error),
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
            }
        }
        notify_message_event.delay(payload)
    except Exception:
        pass