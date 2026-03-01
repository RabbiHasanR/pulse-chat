import io
import os
import time
import magic
import logging
import tempfile
import shutil
from PIL import Image
from botocore.exceptions import ClientError
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFSyntaxError,
)
from utils.aws import s3

logger = logging.getLogger(__name__)

# ─── Security & Performance Thresholds ────────────────────────────────────────

MAX_PDF_DOWNLOAD_BYTES = 50 * 1024 * 1024   # 50 MB  — compressed file size guard
THUMBNAIL_MAX_PX = 500                       # Max dimension for generated thumbnail
THUMBNAIL_DPI = 75                           # Low DPI is sufficient for a 500px thumb
THUMBNAIL_QUALITY = 85                       # JPEG quality

# Pillow decompression bomb guard.
# Raises PIL.Image.DecompressionBombError if rendered image exceeds this.
# 50 MP ≈ 7000×7000 px — generous but safe ceiling.
Image.MAX_IMAGE_PIXELS = 50_000_000

FORBIDDEN_MIME_TYPES = frozenset({
    "application/x-dosexec",
    "application/x-executable",
    "application/x-sh",
    "text/x-python",
    "text/javascript",
    "text/html",
})


# ─── Processor ────────────────────────────────────────────────────────────────

class FileProcessor:
    """
    Processes a raw uploaded asset stored in S3.

    Responsibilities:
      1. Security check  — MIME sniffing on the first 2 KB (zero full-download).
      2. Metadata        — File size extracted from the same single S3 call.
      3. PDF preview     — Thumbnail + page count, only for PDFs under the size limit.

    Design goals:
      - Stateless & thread-safe  → safe to run in Celery workers / Lambda.
      - Minimal disk usage       → temp dir created only when a PDF is processed.
      - Idempotent               → re-running on the same asset is safe.
    """

    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key
        self._temp_dir = None   # Lazy — only created if a PDF download is needed.

    # ── Public API ─────────────────────────────────────────────────────────────

    def process(self) -> dict:
        """
        Orchestrates the full processing pipeline.

        Returns:
            {
                "object_key": str,
                "file_size":  int,
                "variants":   dict   # type, mime_type, file_size, preview metadata …
            }

        Raises:
            ValueError  — forbidden / undetectable MIME type.
            Exception   — any unrecoverable S3 or runtime error.
        """
        start_time = time.monotonic()

        try:
            logger.info("file_processing_started", extra={"asset_id": str(self.asset.id)})

            # Step 1 — Single S3 round-trip: security check + file size.
            mime_type, file_size = self._validate_and_extract_metadata(self.original_key)

            variants = {
                "type": "file",
                "mime_type": mime_type,
                "file_size": file_size,
                "is_preview_available": False,
            }

            # Step 2 — PDF-specific processing (conditional download).
            if mime_type == "application/pdf":
                variants.update(self._handle_pdf(file_size))

            # Step 3 — Structured success log.
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "file_processing_complete",
                extra={
                    "asset_id": str(self.asset.id),
                    "mime_type": mime_type,
                    "file_size": file_size,
                    "duration_ms": duration_ms,
                    "preview_generated": variants.get("is_preview_available"),
                },
            )

            return {
                "object_key": self.original_key,
                "file_size": file_size,
                "variants": variants,
            }

        except Exception:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(
                "file_processing_failed",
                extra={"asset_id": str(self.asset.id), "duration_ms": duration_ms},
            )
            raise

        finally:
            self._cleanup_temp_dir()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _validate_and_extract_metadata(self, key: str) -> tuple[str, int]:
        """
        Fetches only the first 2 KB from S3 to:
          a) Detect the real MIME type via libmagic.
          b) Extract the total file size from the Content-Range header.

        Returns:
            (mime_type, file_size)

        Raises:
            ValueError — on forbidden MIME type or S3/magic failure.
        """
        try:
            response = s3.get_object(
                Bucket=self.bucket,
                Key=key,
                Range="bytes=0-2048",
            )

            # Content-Range: bytes 0-2048/TOTAL  — parse the total.
            # Falls back to ContentLength if range header is absent or unknown (*).
            file_size = self._parse_file_size(response)

            head_bytes = response["Body"].read()
            mime = magic.from_buffer(head_bytes, mime=True)

            if mime in FORBIDDEN_MIME_TYPES:
                raise ValueError(f"Security Alert: Forbidden file type '{mime}'")

            return mime, file_size

        except ValueError:
            # Re-raise intentional security errors without wrapping.
            raise
        except ClientError as e:
            raise ValueError(f"S3 Error during security check: {e}") from e
        except magic.MagicException as e:
            raise ValueError(f"MIME detection failed: {e}") from e

    @staticmethod
    def _parse_file_size(s3_response: dict) -> int:
        """
        Safely extracts total file size from an S3 GetObject response.

        S3 returns Content-Range: bytes 0-2048/TOTAL when a Range is requested.
        The total can be '*' (unknown) per RFC 7233 — handle that gracefully.
        """
        content_range = s3_response.get("ContentRange", "")
        if content_range:
            total = content_range.split("/")[-1]
            if total.isdigit():
                return int(total)
        # Fallback: no range header, or unknown total.
        return s3_response.get("ContentLength", 0)

    def _handle_pdf(self, file_size: int) -> dict:
        """
        Orchestrates PDF-specific processing with size guard.

        Returns a dict to merge into `variants`.
        """
        if file_size > MAX_PDF_DOWNLOAD_BYTES:
            logger.info(
                "pdf_skipped_too_large",
                extra={
                    "asset_id": str(self.asset.id),
                    "file_size": file_size,
                    "limit": MAX_PDF_DOWNLOAD_BYTES,
                },
            )
            return {"error": "File too large for preview generation"}

        # Idempotency check — skip re-rendering if thumbnail already exists.
        thumb_key = f"processed/{self.asset.id}/thumbnail.jpg"
        if self._s3_object_exists(thumb_key):
            logger.info(
                "pdf_thumbnail_already_exists",
                extra={"asset_id": str(self.asset.id), "thumb_key": thumb_key},
            )
            # We still need page_count — re-extract it cheaply.
            # If we truly want to skip ALL work we'd need to store page_count elsewhere.
            # For now, return a partial result so callers know preview is available.
            return {"thumbnail": thumb_key, "is_preview_available": True}

        try:
            local_path = os.path.join(self.temp_dir, "input.pdf")
            s3.download_file(self.bucket, self.original_key, local_path)
            return self._process_pdf(local_path, thumb_key)

        except (PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError, OSError, Image.DecompressionBombError) as e:
            logger.warning(
                "pdf_preview_failed",
                extra={"asset_id": str(self.asset.id), "error": str(e)},
            )
            return {"error": "Preview unavailable"}

    def _process_pdf(self, local_path: str, thumb_key: str) -> dict:
        """
        Renders a thumbnail and extracts page count from a local PDF file.

        Cheaper operation (pdfinfo) runs first so we can short-circuit early
        on zero-page or corrupt documents before the expensive render.

        Returns a dict to merge into `variants`.
        """
        # 1. Page count first (subprocess, cheap — no pixel data).
        info = pdfinfo_from_path(local_path)
        page_count = int(info.get("Pages", 0))

        if page_count == 0:
            logger.warning("pdf_zero_pages", extra={"asset_id": str(self.asset.id)})
            return {"page_count": 0}

        # 2. Render only page 1 at low DPI (fast + low RAM).
        pages = convert_from_path(
            local_path,
            first_page=1,
            last_page=1,
            fmt="jpeg",
            dpi=THUMBNAIL_DPI,
        )
        if not pages:
            return {"page_count": page_count}

        # 3. Resize in-memory (no temp file written to disk).
        cover_image = pages[0]
        cover_image.thumbnail((THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX))

        thumb_io = io.BytesIO()
        cover_image.save(thumb_io, format="JPEG", quality=THUMBNAIL_QUALITY)
        thumb_io.seek(0)

        # 4. Upload thumbnail directly from RAM.
        s3.upload_fileobj(
            thumb_io,
            self.bucket,
            thumb_key,
            ExtraArgs={
                "ContentType": "image/jpeg",
                "CacheControl": "max-age=31536000",
            },
        )

        return {
            "thumbnail": thumb_key,
            "page_count": page_count,
            "is_preview_available": True,
        }

    def _s3_object_exists(self, key: str) -> bool:
        """Returns True if the S3 object already exists (idempotency check)."""
        try:
            s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    # ── Temp dir lifecycle ─────────────────────────────────────────────────────

    @property
    def temp_dir(self) -> str:
        """Lazy temp directory — only created when a PDF download is needed."""
        if self._temp_dir is None:
            self._temp_dir = tempfile.mkdtemp()
        return self._temp_dir

    def _cleanup_temp_dir(self):
        """Removes the temp directory only if it was actually created."""
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir)
            self._temp_dir = None