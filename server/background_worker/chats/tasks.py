from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
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

@shared_task
def notify_message_event(payload: dict):
    data = payload.get("data", {})
    message_id = data.get("message_id")
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    
    status = data.get("status", "pending")
    processing_status = data.get("processing_status", "queued")

    if not message_id or not sender_id or not receiver_id:
        return

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

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
# 2. PROCESSING TASK
# ----------------------------------------------------------------------------
@shared_task(
    bind=True, 
    acks_late=True,             # Retry if worker crashes mid-task
    reject_on_worker_lost=True, # Re-queue immediately on power loss
    max_retries=3
)
def process_uploaded_asset(self, asset_id):
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        last_ws_progress = 0
        last_db_progress = 0

        # --- PROGRESS HANDLER ---
        def on_progress(percent, thumb_key=None):
            nonlocal last_ws_progress, last_db_progress
            
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

            # WebSocket Throttle (2%)
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

            # DB Throttle (10%)
            if abs(percent - last_db_progress) >= 10:
                last_db_progress = percent
                MediaAsset.objects.filter(id=asset.id).update(
                    processing_progress=percent,
                    processing_status="running"
                )

        # --- CHECKPOINT HANDLER (New) ---
        def on_checkpoint(variant_name):
            # Refresh to get latest state in case of concurrent writes
            asset.refresh_from_db()
            current_vars = asset.variants or {}
            
            # Init nested dict if missing
            if 'hls_parts' not in current_vars:
                current_vars['hls_parts'] = {}
            
            # Mark this resolution as done
            current_vars['hls_parts'][variant_name] = True
            
            asset.variants = current_vars
            asset.save(update_fields=['variants'])
            print(f"Checkpoint saved: {variant_name}")
        # --------------------------------

        # Init Status
        asset.processing_status = "running"
        asset.save(update_fields=["processing_status"])

        result_data = {}
        
        if asset.kind == MediaAsset.Kind.IMAGE:
            processor = ImageProcessor(asset)
            result_data = processor.process()
            
        elif asset.kind == MediaAsset.Kind.VIDEO:
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
                    "hls_parts": asset.variants.get('hls_parts', {}) # Keep checkpoint data
                }
            }

        # Apply Results
        if result_data:
            asset.object_key = result_data.get("object_key", asset.object_key)
            if "width" in result_data: asset.width = result_data["width"]
            if "height" in result_data: asset.height = result_data["height"]
            if "file_size" in result_data: asset.file_size = result_data["file_size"]
            
            existing_vars = asset.variants or {}
            existing_vars.update(result_data.get("variants", {}))
            asset.variants = existing_vars

        # Done
        asset.processing_status = "done"
        asset.processing_progress = 100.0
        asset.save()

        if msg.status == 'pending':
            msg.status = 'sent'
            msg.save(update_fields=["status", "updated_at"])

        # Final Notify
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
        print(f"Processing Failed for Asset {asset_id}: {e}")
        try:
            asset = MediaAsset.objects.get(id=asset_id)
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
                    "error": str(e),
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                }
            }
            notify_message_event.delay(payload)
        except Exception:
            pass