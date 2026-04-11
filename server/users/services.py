import uuid
import logging
import mimetypes
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

        put_url = generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": AWS_BUCKET,
                "Key": object_key,
                "ContentType": content_type
            },
            ExpiresIn=DEFAULT_EXPIRES_DIRECT # e.g. 300 seconds
        )

        return {
            "upload_url": put_url,
            "object_key": object_key
        }

    @staticmethod
    def confirm_avatar_update(user, temp_key):

        expected_prefix = f"avatars/temp/user_{user.id}_"
        if not temp_key.startswith(expected_prefix):
             raise ValueError("Invalid Key: You can only confirm your own uploads.")


        new_key = temp_key.replace("avatars/temp/", "avatars/active/")


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

        user.avatar_bucket = AWS_BUCKET
        user.avatar_key = new_key
        user.save(update_fields=['avatar_bucket', 'avatar_key'])
        
        return user.avatar_url