import uuid
import logging
import mimetypes
from django.core.cache import cache
from utils.s3 import s3, AWS_BUCKET, DEFAULT_EXPIRES_DIRECT, generate_presigned_url

logger = logging.getLogger(__name__)


class AvatarService:
    @staticmethod
    def generate_avatar_upload_url(user, file_name, content_type):
        ext = mimetypes.guess_extension(content_type)
        if not ext:
            ext = ".bin"

        unique_id = uuid.uuid4().hex
        object_key = f"avatars/temp/user_{user.id}_{unique_id}{ext}"

        # Cache the issued key so confirm_avatar_update can verify it exactly.
        # TTL matches the presigned URL expiry so stale keys auto-expire.
        cache.set(f"avatar_pending:{user.id}", object_key, timeout=DEFAULT_EXPIRES_DIRECT)

        put_url = generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": AWS_BUCKET,
                "Key": object_key,
                "ContentType": content_type
            },
            ExpiresIn=DEFAULT_EXPIRES_DIRECT,
        )

        return {
            "upload_url": put_url,
            "object_key": object_key,
        }

    @staticmethod
    def confirm_avatar_update(user, temp_key):
        pending_key = cache.get(f"avatar_pending:{user.id}")
        if pending_key != temp_key:
            raise ValueError("Invalid key: does not match the pending upload for this user.")

        # Build active key from the unique portion — no fragile string replacement.
        filename = temp_key.split("/")[-1]
        new_key = f"avatars/active/{filename}"


        s3.copy_object(
            Bucket=AWS_BUCKET,
            CopySource={'Bucket': AWS_BUCKET, 'Key': temp_key},
            Key=new_key
        )
        
        try:
            s3.delete_object(Bucket=AWS_BUCKET, Key=temp_key)
        except Exception:
            # Non-critical: copy succeeded, DB will be updated. Log for S3 cleanup audit.
            logger.warning("Failed to delete temp avatar key %s — orphaned in S3", temp_key)

        if user.avatar_key and user.avatar_key != new_key:
            try:
                s3.delete_object(Bucket=AWS_BUCKET, Key=user.avatar_key)
            except Exception:
                # Non-critical: old avatar orphaned in S3. Log for cleanup audit.
                logger.warning("Failed to delete old avatar key %s for user %s — orphaned in S3", user.avatar_key, user.id)

        cache.delete(f"avatar_pending:{user.id}")

        user.avatar_bucket = AWS_BUCKET
        user.avatar_key = new_key
        user.save(update_fields=['avatar_bucket', 'avatar_key'])

        return user.avatar_url