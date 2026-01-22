import uuid
from django.conf import settings
from utils.s3 import s3, AWS_BUCKET, DEFAULT_EXPIRES_DIRECT

class AvatarService:
    @staticmethod
    def generate_avatar_upload_url(user, file_name, content_type):
        """
        Generates a Direct Upload (PUT) URL for the 'temp' folder.
        """
        ext = file_name.split('.')[-1]
        # Use a random ID to prevent collisions in the temp folder
        unique_id = uuid.uuid4().hex
        
        # 1. PATH STRATEGY: Upload to 'temp' first.
        # S3 Lifecycle rule should delete 'avatars/temp/*' after 24h.
        object_key = f"avatars/temp/user_{user.id}_{unique_id}.{ext}"

        # 2. GENERATE SIGNED URL
        # We lock the Content-Type to prevent spoofing.
        put_url = s3.generate_presigned_url(
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
        """
        Moves the file from 'temp/' to 'active/' and updates the User DB.
        """
        # 1. SECURITY CHECK: Ensure user is only touching their own temp file
        expected_prefix = f"avatars/temp/user_{user.id}_"
        if not temp_key.startswith(expected_prefix):
             raise ValueError("Invalid Key: You can only confirm your own uploads.")

        # 2. DEFINE NEW PERMANENT PATH
        # We keep the unique ID to ensure the URL changes (busting CDN cache)
        # Old: avatars/temp/user_101_abc123.jpg -> New: avatars/active/user_101_abc123.jpg
        new_key = temp_key.replace("avatars/temp/", "avatars/active/")

        # 3. MOVE OBJECT (Copy + Delete)
        # S3 does not have a "Move" command, so we Copy then Delete.
        s3.copy_object(
            Bucket=AWS_BUCKET,
            CopySource={'Bucket': AWS_BUCKET, 'Key': temp_key},
            Key=new_key
        )
        
        # Fire-and-Forget Delete (If it fails, Lifecycle rule cleans it up anyway)
        try:
            s3.delete_object(Bucket=AWS_BUCKET, Key=temp_key)
        except Exception:
            pass

        # 4. OPTIONAL: CLEANUP OLD AVATAR
        # If the user already had an avatar, delete it to save space.
        if user.avatar_key and user.avatar_key != new_key:
            try:
                s3.delete_object(Bucket=AWS_BUCKET, Key=user.avatar_key)
            except Exception:
                pass 

        # 5. UPDATE DB
        user.avatar_bucket = AWS_BUCKET
        user.avatar_key = new_key
        user.save(update_fields=['avatar_bucket', 'avatar_key'])
        
        return user.avatar_url