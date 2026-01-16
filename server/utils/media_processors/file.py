import os
import magic
import logging
import tempfile
import shutil
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFPageCountError
from utils.aws import s3

logger = logging.getLogger(__name__)

class FileProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key
        self.temp_dir = tempfile.mkdtemp()

    def process(self):
        try:
            logger.info(f"Starting File Processing: {self.asset.id}")

            # 1. Remote Security Check & MIME Detection (Zero-Download)
            # We fetch only the first 2KB header.
            mime_type = self._validate_and_detect_mime(self.original_key)
            
            # 2. Get File Size (Remote)
            # We ask S3 for metadata, avoiding a download.
            file_size = self._get_remote_file_size(self.original_key)

            variants = {
                "type": "file",
                "mime_type": mime_type,
                "file_size": file_size,
                "is_preview_available": False
            }

            # 3. Conditional Logic: ONLY Download if PDF
            if mime_type == 'application/pdf':
                try:
                    # Download is required for PDF rendering (Random Access)
                    local_path = os.path.join(self.temp_dir, "input.pdf")
                    s3.download_file(self.bucket, self.original_key, local_path)
                    
                    # Generate Thumbnail & Page Count
                    pdf_meta = self._process_pdf(local_path)
                    variants.update(pdf_meta)
                    
                except Exception as e:
                    logger.warning(f"PDF Preview Generation Failed: {e}")
                    # We don't fail the upload, just the preview.
                    variants["error"] = "Preview unavailable"

            # 4. Success (Return Metadata)
            return {
                "object_key": self.original_key, # Original file stays untouched
                "file_size": file_size,
                "variants": variants
            }

        except Exception as e:
            logger.error(f"File Processor Failed: {e}")
            raise e
        finally:
            # Clean up temp dir (Crucial if PDF was downloaded)
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _validate_and_detect_mime(self, key):
        """
        Fetches 2KB header from S3.
        Returns: Detected MIME type string.
        Raises: ValueError if file is malicious.
        """
        try:
            response = s3.get_object(
                Bucket=self.bucket, 
                Key=key, 
                Range='bytes=0-2048'
            )
            head_bytes = response['Body'].read()
            
            # Use buffer detection (Memory safe)
            mime = magic.from_buffer(head_bytes, mime=True)
            
            forbidden = [
                'application/x-dosexec',
                'application/x-executable',
                'application/x-sh', 
                'text/x-python', 
                'text/javascript',
                'text/html'
            ]
            
            if mime in forbidden:
                raise ValueError(f"Security Alert: Forbidden file type '{mime}'")
                
            return mime
            
        except Exception as e:
            # If magic check fails, we assume security risk
            raise ValueError(f"Security Check Error: {str(e)}")

    def _get_remote_file_size(self, key):
        """
        Gets file size from S3 Metadata (HEAD Object). Zero Download.
        """
        try:
            head = s3.head_object(Bucket=self.bucket, Key=key)
            return head['ContentLength']
        except Exception:
            return 0

    def _process_pdf(self, local_path):
        """
        Generates thumbnail and extracts page count.
        """
        # 1. Convert Page 1 to JPEG
        pages = convert_from_path(local_path, first_page=1, last_page=1, fmt='jpeg')
        if not pages:
            return {"page_count": 0}

        # 2. Save Thumbnail
        thumb_filename = f"thumb_{self.asset.id}.jpg"
        thumb_path = os.path.join(self.temp_dir, thumb_filename)
        
        cover_image = pages[0]
        cover_image.thumbnail((500, 500)) # Resize for UI
        cover_image.save(thumb_path, 'JPEG', quality=85)

        # 3. Upload Thumbnail
        thumb_key = f"processed/{self.asset.id}/thumbnail.jpg"
        with open(thumb_path, 'rb') as f:
            s3.upload_fileobj(f, self.bucket, thumb_key, ExtraArgs={
                "ContentType": "image/jpeg",
                "CacheControl": "max-age=31536000"
            })

        # 4. Get Page Count
        info = pdfinfo_from_path(local_path)
        page_count = info.get("Pages", 1)

        return {
            "thumbnail": thumb_key,
            "page_count": int(page_count),
            "is_preview_available": True
        }