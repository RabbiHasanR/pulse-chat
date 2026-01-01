import io
import uuid
import boto3
from utils.aws import s3, AWS_BUCKET

class BaseProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key

    def download_content(self) -> io.BytesIO:
        file_stream = io.BytesIO()
        try:
            s3.download_fileobj(self.bucket, self.original_key, file_stream)
            file_stream.seek(0)
            return file_stream
        except Exception as e:
            print(f"Error downloading {self.original_key}: {e}")
            raise e

    def upload_content(self, file_obj: io.BytesIO, content_type: str, suffix: str) -> str:
        # We preserve the original folder structure: chat_uploads/{user_id}/{uuid}/
        path_parts = self.original_key.split("/")
        folder_path = "/".join(path_parts[:-1]) 
        
        # Create a unique filename for the variant
        # Example: a1b2c3d4_optimized.webp
        new_filename = f"{uuid.uuid4().hex}_{suffix}"
        
        # Append correct extension based on content type
        if "image" in content_type:
            # We assume we are converting images to WebP
            if "webp" in content_type:
                new_filename += ".webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                new_filename += ".jpg"
            else:
                new_filename += ".img"
        elif "video" in content_type:
            new_filename += ".mp4"
            
        new_key = f"{folder_path}/{new_filename}"

        # Reset stream pointer before upload
        file_obj.seek(0)
        
        try:
            s3.upload_fileobj(
                file_obj,
                self.bucket,
                new_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": "max-age=31536000" # Browser should cache this forever (1 year)
                }
            )
            return new_key
        except Exception as e:
            print(f"Error uploading {new_key}: {e}")
            raise e

    def delete_original(self):
        """
        Optional: Call this if you decide to delete the raw file to save storage cost.
        """
        try:
            s3.delete_object(Bucket=self.bucket, Key=self.original_key)
        except Exception as e:
            print(f"Warning: Could not delete original file {self.original_key}: {e}")