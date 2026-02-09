import asyncio
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q, Max
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from botocore.exceptions import BotoCoreError, ClientError
from socket import timeout as SocketTimeout
from celery.exceptions import SoftTimeLimitExceeded, MaxRetriesExceededError

from utils.redis_client import sync_redis_client, RedisKeys 
from chats.models import ChatMessage, MediaAsset

# Processors
from utils.media_processors.image import ImageProcessor
from utils.media_processors.video import VideoProcessor
from utils.media_processors.audio import AudioProcessor
from utils.media_processors.file import FileProcessor

# --- HELPERS ---

def room(user_id):
    return f"user_{user_id}"

def _send_socket_update_directly(user_id, payload):
    """
    Optimized helper for high-frequency updates (Progress Bars).
    Bypasses Celery/Redis Queue. Sends directly to Channels Layer.
    """
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(room(user_id), {
            "type": "forward_event",
            "payload": payload,
        })
    except Exception as e:
        # Log silently, don't crash the task for a socket error
        print(f"⚠️ Direct Socket Push Failed: {e}")

# ----------------------------------------------------------------------------
# 1. GENERAL NOTIFICATION (Kept for HTTP View Usage)
# ----------------------------------------------------------------------------
@shared_task(queue='default', ignore_result=True)
def notify_message_event(payload: dict):
    """
    Used by HTTP Views to send the initial "New Message" signal asynchronously.
    NOT used by Media Tasks anymore (they use direct push).
    """
    data = payload.get("data", {})
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    
    if not sender_id or not receiver_id: return

    # 1. Direct Push
    _send_socket_update_directly(sender_id, payload)
    _send_socket_update_directly(receiver_id, payload)

    # 2. "Seen" Logic (Only if user is currently looking)
    # We do this here to avoid blocking the HTTP response time
    message_id = data.get("message_id")
    if message_id and data.get("status") == "sent":
        is_online = sync_redis_client.sismember(RedisKeys.ONLINE_USERS, receiver_id)
        if is_online:
            viewing_key = RedisKeys.viewing(receiver_id, sender_id)
            if sync_redis_client.scard(viewing_key) > 0:
                # Atomically update DB
                ChatMessage.objects.filter(id=message_id).update(status="seen")
                
                # Send Read Receipt to Sender
                read_receipt = {
                    "type": "chat_read_receipt",
                    "data": {
                        "message_id": message_id,
                        "conversation_id": data.get("conversation"), 
                        "reader_id": receiver_id,
                        "last_read_id": message_id
                    }
                }
                _send_socket_update_directly(sender_id, read_receipt)

# ----------------------------------------------------------------------------
# 2. MARK DELIVERED (On User Connect)
# ----------------------------------------------------------------------------
@shared_task(ignore_result=True, time_limit=10, expires=60)
def mark_delivered_and_notify_senders(user_id):
    """
    Runs when a user connects via WebSocket.
    Marks all 'SENT' messages as 'DELIVERED'.
    """
    pending_groups = ChatMessage.objects.filter(
        receiver_id=user_id,
        status=ChatMessage.Status.SENT
    ).values('sender_id').annotate(last_id=Max('id'))

    if not pending_groups: return

    ChatMessage.objects.filter(
        receiver_id=user_id,
        status=ChatMessage.Status.SENT
    ).update(status=ChatMessage.Status.DELIVERED)

    # Notify all senders that their messages were delivered
    for entry in pending_groups:
        event = {
            "type": "chat_delivery_receipt",
            "data": {
                "receiver_id": user_id,
                "last_delivered_id": entry['last_id']
            }
        }
        _send_socket_update_directly(entry['sender_id'], event)

# ----------------------------------------------------------------------------
# 3. OPTIMIZED FINALIZER (Shared by all media tasks)
# ----------------------------------------------------------------------------
def _finalize_asset(asset, msg, result_data):
    """
    OPTIMIZED: Accepts objects (asset, msg) to avoid DB lookups.
    Performs one atomic commit at the end.
    """
    try:
        # 1. Update Asset Data (Memory)
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

        # 2. Determine Message Status (Redis Check)
        new_status = msg.status
        
        # Check if Receiver is Viewing/Online (Redis is fast)
        viewing_key = RedisKeys.viewing(msg.receiver_id, msg.sender_id)
        if sync_redis_client.scard(viewing_key) > 0:
            new_status = 'seen'
        elif msg.status == 'sent': 
            if sync_redis_client.sismember(RedisKeys.ONLINE_USERS, msg.receiver_id):
                new_status = 'delivered'

        # 3. ATOMIC COMMIT (1 Write for Asset, 1 Optional for Msg)
        with transaction.atomic():
            asset.save()
            if new_status != msg.status:
                msg.status = new_status
                msg.save(update_fields=['status', 'updated_at'])

        # 4. WebSocket Broadcast
        payload = {
            "type": "chat_message_update",
            "success": True,
            "data": {
                "message_id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "asset_id": asset.id, 
                "kind": asset.kind,
                "status": new_status,
                "processing_status": "done",
                "media_url": asset.url,
                "thumbnail_url": asset.thumbnail_url,
                "width": asset.width,
                "height": asset.height,
            }
        }
        _send_socket_update_directly(msg.receiver_id, payload)
        _send_socket_update_directly(msg.sender_id, payload)

        # 5. Read Receipt (If upgraded to Seen)
        if new_status == 'seen' and msg.status != 'seen':
             read_receipt = {
                "type": "chat_read_receipt",
                "data": {
                    "message_id": msg.id,
                    "reader_id": msg.receiver_id,
                    "last_read_id": msg.id
                }
            }
             _send_socket_update_directly(msg.sender_id, read_receipt)

    except Exception as e:
        print(f"❌ Finalize Failed for Asset {asset.id}: {e}")
        # Recommendation: Use Sentry here

# ----------------------------------------------------------------------------
# 4. VIDEO TASK (Resume-on-Retry + In-Memory)
# ----------------------------------------------------------------------------
@shared_task(
    bind=True, 
    queue='video_queue',
    acks_late=True,
    reject_on_worker_lost=False,
    soft_time_limit=3600, # 1 Hour
    time_limit=3660,
    max_retries=3
)
def process_video_task(self, asset_id):
    checkpoint_key = f"video_checkpoint:{asset_id}"
    progress_key = f"asset_progress:{asset_id}"
    
    try:
        # --- PHASE 1: INITIALIZATION (Read Only) ---
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # Check Redis for Crash Recovery
        saved_state = cache.get(checkpoint_key)
        if saved_state:
            print(f"🔄 Resuming asset {asset_id} from Redis checkpoint...")
            asset.variants = saved_state.get('variants', {})
        else:
            # First run: Set status to running
            MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
            asset.variants = asset.variants or {}

        # Local Accumulators (No DB Writes)
        local_variants = asset.variants
        last_sent_progress = cache.get(progress_key, 0)

        # --- PHASE 2: PROCESSING (Memory Only) ---

        def on_progress(percent, thumb_key=None):
            nonlocal last_sent_progress
            cache.set(progress_key, percent, timeout=3600)
            
            should_send = False
            update_data = {
                "message_id": msg.id, 
                "asset_id": asset.id, 
                "processing_status": "running"
            }

            if thumb_key:
                # Update Local State & Checkpoint
                local_variants['thumbnail'] = thumb_key
                asset.variants = local_variants # Update obj for URL generation
                
                # Save to Redis (Critical for retry)
                cache.set(checkpoint_key, {'variants': local_variants}, timeout=7200)
                
                update_data["thumbnail_url"] = asset.get_thumbnail_url()
                update_data["stage"] = "thumbnail_ready"
                should_send = True

            if abs(percent - last_sent_progress) >= 2 or should_send:
                last_sent_progress = percent
                update_data["progress"] = round(percent, 1)
                should_send = True

            if should_send:
                _send_socket_update_directly(msg.receiver_id, {"type": "chat_message_update", "data": update_data})
                _send_socket_update_directly(msg.sender_id, {"type": "chat_message_update", "data": update_data})

        def on_checkpoint(variant_name):
            """Saves HLS part completion to Redis."""
            if 'hls_parts' not in local_variants: local_variants['hls_parts'] = {}
            local_variants['hls_parts'][variant_name] = True
            
            # 🔥 Save to Redis (TTL 2 hours)
            cache.set(checkpoint_key, {'variants': local_variants}, timeout=7200)

        def on_playable(master_key):
            """Notify UI that playback is possible."""
            asset.object_key = master_key
            update_data = {
                "message_id": msg.id,
                "asset_id": asset.id,
                "video_url": asset.url,
                "processing_status": "running", 
                "stage": "playable"
            }
            _send_socket_update_directly(msg.receiver_id, {"type": "chat_message_update", "data": update_data})
            _send_socket_update_directly(msg.sender_id, {"type": "chat_message_update", "data": update_data})

        # --- EXECUTION ---
        processor = VideoProcessor(asset)
        
        # This takes 10-20 mins but hits DB Zero times
        master_key, thumb_key = processor.process(
            on_progress_callback=on_progress,
            on_checkpoint_save=on_checkpoint,
            on_playable_callback=on_playable
        )
        
        # --- PHASE 3: FINALIZE (Write to DB) ---
        result_data = {
            "object_key": master_key, 
            "variants": {
                "type": "hls", 
                "master": master_key, 
                "thumbnail": thumb_key,
                "hls_parts": local_variants.get('hls_parts', {})
            }
        }
        
        _finalize_asset(asset, msg, result_data)
        
        # Cleanup
        cache.delete(progress_key)
        cache.delete(checkpoint_key)

    except (BotoCoreError, ClientError, SocketTimeout, ConnectionError) as e:
        try:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
            _handle_failure(asset_id, f"Max retries exceeded: {e}")
            cache.delete(checkpoint_key)
    except SoftTimeLimitExceeded:
        _handle_failure(asset_id, "Time limit exceeded")
    except Exception as e:
        _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 5. IMAGE / AUDIO / FILE TASKS (Direct Socket + Optimized Finalizer)
# ----------------------------------------------------------------------------

@shared_task(bind=True, queue='image_queue', acks_late=True, max_retries=3)
def process_image_task(self, asset_id):
    try:
        # 1. Quick Update to Running
        MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        
        # 2. Fetch Objects
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        
        # 3. Process
        processor = ImageProcessor(asset)
        result_data = processor.process()
        
        # 4. Finalize (Passing objects!)
        _finalize_asset(asset, asset.message, result_data)

    except (BotoCoreError, ClientError) as e:
        try:
             raise self.retry(exc=e, countdown=5 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
             _handle_failure(asset_id, f"AWS Error: {e}")
    except Exception as e:
        _handle_failure(asset_id, e)

@shared_task(bind=True, queue='audio_queue', acks_late=True, max_retries=3)
def process_audio_task(self, asset_id):
    try:
        MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message

        # Notify Start via Socket (Direct)
        _send_socket_update_directly(msg.sender_id, {
             "type": "chat_message_update", 
             "data": {"message_id": msg.id, "asset_id": asset.id, "processing_status": "running"}
        })

        processor = AudioProcessor(asset)
        result_data = processor.process()
        _finalize_asset(asset, msg, result_data)

    except Exception as e:
        try:
             raise self.retry(exc=e, countdown=10)
        except Exception:
             _handle_failure(asset_id, e)

@shared_task(bind=True, queue='file_queue', acks_late=True, max_retries=3)
def process_file_task(self, asset_id):
    try:
        MediaAsset.objects.filter(id=asset_id).update(processing_status="running")
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        _send_socket_update_directly(msg.sender_id, {
             "type": "chat_message_update", 
             "data": {"message_id": msg.id, "asset_id": asset.id, "processing_status": "running"}
        })
        
        processor = FileProcessor(asset)
        result_data = processor.process()
        _finalize_asset(asset, msg, result_data)
        
    except Exception as e:
        try:
             raise self.retry(exc=e, countdown=10)
        except Exception:
             _handle_failure(asset_id, e)

# ----------------------------------------------------------------------------
# 6. FAILURE & CLEANUP (Robust)
# ----------------------------------------------------------------------------

def _handle_failure(asset_id, error):
    print(f"❌ Processing Failed for Asset {asset_id}: {error}")
    try:
        asset = MediaAsset.objects.select_related("message").get(id=asset_id)
        msg = asset.message
        
        # Partial Success Check (Video HLS)
        hls_parts = asset.variants.get('hls_parts', {}) if asset.variants else {}
        is_playable = bool(hls_parts)
        
        new_status = "done" if is_playable else "failed"
        
        # Update Asset
        if not asset.variants: asset.variants = {}
        asset.variants['error_log'] = str(error)
        asset.processing_status = new_status
        asset.save()

        # Update Message
        with transaction.atomic():
            msg.refresh_from_db()
            
            # Check if message is "Dead" (No text + No valid media)
            # We assume current asset is failed unless playable
            valid_assets_count = msg.media_assets.filter(
                Q(processing_status='done') | Q(processing_status='running')
            ).count()
            
            is_dead = (not msg.content) and (valid_assets_count == 0) and (not is_playable)

            if is_dead:
                msg.status = 'failed'
                msg.save(update_fields=['status'])
            elif msg.status == 'pending':
                # If partial success, ensure it's sent
                msg.status = 'sent'
                msg.save(update_fields=['status'])

        # Notify via Socket
        payload = {
            "type": "chat_message_update",
            "success": False,
            "data": {
                "message_id": msg.id,
                "asset_id": asset.id,
                "status": msg.status,
                "processing_status": new_status,
                "error": str(error)
            }
        }
        _send_socket_update_directly(msg.receiver_id, payload)
        _send_socket_update_directly(msg.sender_id, payload)
        
    except Exception as e:
        print(f"CRITICAL: Failed to handle failure: {e}")

@shared_task(bind=True, queue='default', acks_late=True, soft_time_limit=60)
def cleanup_stuck_assets(self):
    """
    Periodic Zombie Killer.
    Handles assets stuck in 'queued' (Abandoned) or 'running' (Crashed).
    """
    try:
        now = timezone.now()
        abandoned_limit = now - timedelta(hours=24)
        crash_limit = now - timedelta(hours=2)

        stuck_assets = MediaAsset.objects.filter(
            Q(processing_status='queued', created_at__lte=abandoned_limit) |
            Q(processing_status='running', created_at__lte=crash_limit)
        ).select_related('message')

        if not stuck_assets.exists(): return "Clean"

        msg_ids = set(stuck_assets.values_list('message_id', flat=True))
        
        # Bulk Fail Assets
        stuck_assets.update(processing_status='failed', variants={'error': 'Timeout/Crash'})

        # Notify Affected Users
        msgs = ChatMessage.objects.filter(id__in=msg_ids)
        for msg in msgs:
            # Re-evaluate message status
            valid = msg.media_assets.filter(processing_status='done').exists()
            if not msg.content and not valid:
                msg.status = 'failed'
                msg.save()
            
            payload = {
                "type": "chat_message_update",
                "data": {
                    "message_id": msg.id,
                    "status": msg.status,
                    "processing_status": "failed"
                }
            }
            _send_socket_update_directly(msg.sender_id, payload)
            _send_socket_update_directly(msg.receiver_id, payload)

        return f"Cleaned {len(stuck_assets)} assets"

    except Exception as e:
        print(f"Cleanup Error: {e}")