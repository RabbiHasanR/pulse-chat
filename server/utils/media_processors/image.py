import io
import uuid
import logging
import magic
from PIL import Image, ImageOps, UnidentifiedImageError
from botocore.exceptions import ClientError
from utils.aws import s3

logger = logging.getLogger(__name__)

# --- SAFETY LIMITS ---
MAX_FILE_SIZE_MB = 25       # Reject images larger than 25MB
MAX_PIXEL_COUNT = 89478485  # Standard Pillow Limit (Protect against Decompression Bombs)

class ImageProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key

    def process(self):
        try:
            # 1. REMOTE VALIDATION (Security + Size Check)
            # Checks Magic Bytes AND File Size header
            self._validate_remote_header()

            # 2. DOWNLOAD TO RAM
            # Safe because we capped size at 25MB
            raw_stream = io.BytesIO()
            try:
                s3.download_fileobj(self.bucket, self.original_key, raw_stream)
            except ClientError as e:
                # Handle race condition where user deletes file mid-process
                raise ValueError(f"Failed to download asset: {str(e)}")
            
            raw_stream.seek(0)

            # 3. OPEN & DEEP VALIDATION
            try:
                img = Image.open(raw_stream)
                
                # Check for Decompression Bombs (Massive dimensions)
                if img.width * img.height > MAX_PIXEL_COUNT:
                    raise ValueError("Image dimensions too large for processing")
                
                img.verify() 
                
                raw_stream.seek(0)
                img = Image.open(raw_stream)
                img.load()
            except UnidentifiedImageError:
                self._delete_original()
                raise ValueError("Security Alert: Pillow cannot identify image file")
            except Exception as e:
                self._delete_original()
                raise ValueError(f"Corrupt or unsafe image: {e}")

            # ... (Rest of processing logic: Rotate, Resize, Upload) ...
            # ... Copy from previous response ...
             # 4. PROCESSING (Rotate -> Resize -> WebP)
            # Fix Orientation (Mobile photos often have EXIF rotation)
            img = ImageOps.exif_transpose(img)
            original_w, original_h = img.size

            # Generate Main Image
            optimized_stream, opt_w, opt_h = self._resize_and_compress(
                img, 1920 # MAX_DIMENSION
            )
            
            # Generate Thumbnail
            thumb_stream, _, _ = self._resize_and_compress(
                img, 300 # THUMB_DIMENSION
            )

            # 5. UPLOAD VARIANTS
            # Calculate final size for DB
            optimized_stream.seek(0, 2)
            new_size = optimized_stream.tell()
            optimized_stream.seek(0)

            main_key = self._upload_variant(optimized_stream, suffix="optimized")
            thumb_key = self._upload_variant(thumb_stream, suffix="thumb")

            # 6. CLEANUP
            self._delete_original()

            return {
                "object_key": main_key,
                "content_type": "image/webp",
                "file_size": new_size,
                "width": opt_w,
                "height": opt_h,
                "variants": {
                    "type": "image",
                    "thumbnail": thumb_key,
                    "original_width": original_w,
                    "original_height": original_h
                }
            }

        except Exception as e:
            logger.error(f"Image Processing Failed: {e}")
            raise e
        finally:
            if 'raw_stream' in locals():
                raw_stream.close()

    def _validate_remote_header(self):
        """
        Fetches metadata to check SIZE and Magic Bytes.
        """
        try:
            # Step A: Check File Size from S3 Metadata first (Zero Cost)
            head = s3.head_object(Bucket=self.bucket, Key=self.original_key)
            file_size_bytes = head['ContentLength']
            
            if file_size_bytes == 0:
                raise ValueError("File is empty (0 bytes).")
            
            file_size_mb = file_size_bytes / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                # Reject huge files. User should send them as 'Document' type.
                raise ValueError(f"Image too large ({file_size_mb:.1f}MB). Limit is {MAX_FILE_SIZE_MB}MB.")

            # Step B: Check Magic Bytes (Security)
            response = s3.get_object(
                Bucket=self.bucket, 
                Key=self.original_key, 
                Range='bytes=0-2048'
            )
            head_bytes = response['Body'].read()
            mime = magic.from_buffer(head_bytes, mime=True)
            
            allowed = [
                'image/jpeg', 'image/png', 'image/webp', 
                'image/gif', 'image/bmp', 'image/tiff'
            ]
            
            if mime not in allowed:
                raise ValueError(f"Security Alert: Invalid image mime-type '{mime}'")
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == "404":
                raise ValueError("Asset not found in S3 (Upload may have failed).")
            else:
                raise ValueError(f"S3 Validation Error: {error_code}")
        except Exception as e:
            raise ValueError(f"Remote Validation Error: {str(e)}")

    # ... (_resize_and_compress, _upload_variant, _delete_original remain the same) ...
     # Copy the previous implementation of _resize_and_compress, _upload_variant, _delete_original here.
    def _resize_and_compress(self, img: Image, max_dim: int):
        """
        Resizes down, converts to RGB, saves as WebP.
        """
        img_copy = img.copy()
        
        if img_copy.mode not in ("RGB", "RGBA"):
            img_copy = img_copy.convert("RGB")

        # Downscale only (Never upscale)
        current_w, current_h = img_copy.size
        if current_w > max_dim or current_h > max_dim:
            img_copy.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        
        final_w, final_h = img_copy.size
        
        output = io.BytesIO()
        img_copy.save(
            output, 
            format="WEBP", 
            quality=80, 
            optimize=True
        )
        output.seek(0)
        
        return output, final_w, final_h

    def _upload_variant(self, file_obj: io.BytesIO, suffix: str) -> str:
        folder_path = f"processed/{self.asset.id}"
        new_filename = f"{uuid.uuid4().hex}_{suffix}.webp"
        new_key = f"{folder_path}/{new_filename}"

        try:
            s3.upload_fileobj(
                file_obj,
                self.bucket,
                new_key,
                ExtraArgs={
                    "ContentType": "image/webp",
                    "CacheControl": "max-age=31536000"
                }
            )
            return new_key
        except Exception as e:
            logger.error(f"S3 Upload Error: {e}")
            raise e

    def _delete_original(self):
        try:
            s3.delete_object(Bucket=self.bucket, Key=self.original_key)
        except Exception:
            pass