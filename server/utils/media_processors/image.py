import io
from PIL import Image, ImageOps
from .base import BaseProcessor

# --- Configuration for Chat Images ---
MAX_DIMENSION = 1920   # 1080p equivalent
THUMB_DIMENSION = 300  # For message list previews
QUALITY = 80           # Good compression balance
FORMAT = "WEBP"        # Modern format
MIME_TYPE = "image/webp"

class ImageProcessor(BaseProcessor):
    def process(self):
        """
        Main pipeline:
        1. Download Raw Image
        2. Auto-Rotate (Exif data)
        3. Generate 'Optimized' Main Image (WebP)
        4. Generate 'Thumbnail' (WebP)
        5. Upload Both to S3
        6. DELETE Raw File (via BaseProcessor)
        7. Return data to update DB
        """
        raw_stream = self.download_content()
        
        try:
            # Open image
            img = Image.open(raw_stream)
            
            # 1. Fix Orientation (Crucial for mobile photos)
            img = ImageOps.exif_transpose(img)
            
            # 2. Extract Original Dimensions
            original_w, original_h = img.size

            # 3. Process Main Optimized Image
            optimized_stream, opt_w, opt_h = self._resize_and_compress(
                img, MAX_DIMENSION
            )
            
            # 4. Process Thumbnail
            thumb_stream, _, _ = self._resize_and_compress(
                img, THUMB_DIMENSION
            )

            # 5. Upload to S3
            main_key = self.upload_content(optimized_stream, MIME_TYPE, "optimized")
            thumb_key = self.upload_content(thumb_stream, MIME_TYPE, "thumb")

            # -----------------------------------------------------------
            # 6. DELETE RAW FILE (Cost Optimization)
            # -----------------------------------------------------------
            # Use the helper from BaseProcessor
            if main_key and main_key != self.original_key:
                self.delete_original()
            # -----------------------------------------------------------

            # 7. Calculate file size of the new optimized image
            optimized_stream.seek(0, 2)
            new_size = optimized_stream.tell()

            # 8. Return Result
            return {
                "object_key": main_key,       # The optimized WebP is now the MAIN file
                "content_type": MIME_TYPE,    # Updated to image/webp
                "file_size": new_size,
                "width": opt_w,
                "height": opt_h,
                
                "variants": {
                    "thumbnail": thumb_key,
                    
                    # Original file is deleted, but we keep metadata
                    "original_width": original_w,
                    "original_height": original_h
                }
            }

        except Exception as e:
            print(f"Image Processing Failed: {e}")
            raise e
        finally:
            raw_stream.close()

    def _resize_and_compress(self, img: Image, max_dim: int):
        """
        Helper: Resizes image keeping aspect ratio, converts to RGB, saves as WebP.
        """
        img_copy = img.copy()
        
        if img_copy.mode not in ("RGB", "RGBA"):
            img_copy = img_copy.convert("RGB")

        # Smart Resize (LANCZOS is best quality for downscaling)
        img_copy.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        
        width, height = img_copy.size
        
        output = io.BytesIO()
        img_copy.save(
            output, 
            format=FORMAT, 
            quality=QUALITY, 
            optimize=True
        )
        output.seek(0)
        
        return output, width, height