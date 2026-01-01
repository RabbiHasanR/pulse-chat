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

from utils.media_processors.image import ImageProcessor

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

    # ---------------------------------------------------------
    # 1. NOTIFICATIONS (Keep both for multi-device sync)
    # ---------------------------------------------------------
    
    # Notify Sender (Keeps laptop/phone in sync with the uploading device)
    async_to_sync(channel_layer.group_send)(room(sender_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # Notify Receiver (So they see the "Incoming..." bubble)
    async_to_sync(channel_layer.group_send)(room(receiver_id), {
        "type": "forward_event",
        "payload": payload,
    })

    # ---------------------------------------------------------
    # 2. PRESENCE LOGIC (The "Seen" Fix)
    # ---------------------------------------------------------
    
    # Only mark as "SEEN" if the file is actually ready.
    # We skip this logic if the file is still uploading (pending) or processing.
    is_media_ready = (status != "pending") and (processing_status == "done")
    
    # Also apply this logic for normal text messages (which don't have processing_status)
    is_text_message = (data.get("message_type") == "text")
    
    should_check_seen = is_media_ready or is_text_message

    if should_check_seen:
        # Check if receiver is online
        if async_to_sync(redis_client.sismember)("online_users", receiver_id):
            # Check if receiver is currently looking at this chat
            if async_to_sync(is_user_viewing_me)(receiver_id=receiver_id, sender_id=sender_id):
                
                # 1. Update DB
                ChatMessage.objects.filter(id=message_id).update(status="seen")
                
                # 2. Notify Sender: "User saw your message"
                async_to_sync(notify_single_status)(
                    message_id=message_id,
                    receiver_id=receiver_id,
                    sender_id=sender_id,
                    status="seen",
                )
            else:
                # Online but in different chat -> Unread Count ++
                async_to_sync(send_unread_summary)(
                    to_user_id=receiver_id,
                    from_user_id=sender_id,
                )
        else:
            # Offline -> Unread Count ++
            async_to_sync(send_unread_summary)(
                to_user_id=receiver_id,
                from_user_id=sender_id,
            )
            
            


# ----------------------------------------------------------------------------
# 2. PROCESSING TASK (Heavy Lifting)
# ----------------------------------------------------------------------------
@shared_task(bind=True)
def process_uploaded_asset(self, asset_id):
    """
    Background worker to process raw uploads.
    - Resizes/Optimizes Images (WebP)
    - Deletes raw files to save storage
    - Updates DB and notifies Frontend
    """
    try:
        # 1. Fetch Asset & Linked Message
        # We need the message to update its status later
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # 2. Transition Status -> RUNNING
        asset.processing_status = "running"
        asset.processing_progress = 0.0
        asset.save(update_fields=["processing_status", "processing_progress"])
        
        # (Optional) You could notify UI here that processing started
        # _notify_progress(msg, asset, 0.1)

        # 3. SELECT PROCESSOR LOGIC
        result_data = {}
        
        if asset.kind == MediaAsset.Kind.IMAGE:
            # --- IMAGE PROCESSING ---
            processor = ImageProcessor(asset)
            result_data = processor.process() 
            # This returns dict with new object_key, width, height, variants, etc.
            
        elif asset.kind == MediaAsset.Kind.VIDEO:
            # --- VIDEO PROCESSING (Future) ---
            # processor = VideoProcessor(asset)
            # result_data = processor.process()
            pass
            
        # 4. APPLY RESULTS TO DB
        if result_data:
            # Update the main file pointer (e.g. to the optimized WebP)
            asset.object_key = result_data.get("object_key", asset.object_key)
            asset.content_type = result_data.get("content_type", asset.content_type)
            asset.file_size = result_data.get("file_size", asset.file_size)
            
            # Update Dimensions (Critical for UI Layout)
            asset.width = result_data.get("width")
            asset.height = result_data.get("height")
            
            # Update Variants (Thumbnails, metadata)
            asset.variants = result_data.get("variants", {})

        # 5. Transition Status -> DONE
        asset.processing_status = "done"
        asset.processing_progress = 1.0
        asset.save()

        # 6. Update Message Status -> SENT
        # Now that media is ready for viewing, the message is officially "Sent"
        if msg.status == 'pending':
            msg.status = 'sent'
            msg.save(update_fields=["status", "updated_at"])

        # 7. FINAL NOTIFICATION (The "Green Light" for UI)
        # This payload tells the Frontend: "Processing complete. Show the image."
        payload = {
            "type": "chat_message",
            "success": True,
            "data": {
                "message_id": msg.id,
                "message_type": msg.message_type,
                
                # STATUS UPDATE:
                "status": "sent",           # Checkmark update (Pending -> Sent)
                "processing_status": "done",
                "stage": "done",            # UI Instruction: Replace Spinner with Image
                
                # CONTENT UPDATE (URLS):
                "media_url": asset.url,               # Full Size (Optimized)
                "thumbnail_url": asset.thumbnail_url, # Small Size (For list view)
                
                # METADATA:
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
        # 8. FAILURE HANDLING
        print(f"Processing Failed for Asset {asset_id}: {e}")
        
        # Mark as failed in DB so admin can inspect
        try:
            asset = MediaAsset.objects.get(id=asset_id)
            asset.processing_status = "failed"
            asset.save(update_fields=["processing_status"])
            
            # Notify User of Failure
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
            pass # Use logging in production