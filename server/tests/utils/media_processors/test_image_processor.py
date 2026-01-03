import pytest
import io
from PIL import Image
from unittest.mock import patch
from utils.media_processors.image import ImageProcessor

# We patch the 's3' client imported in base.py to use our moto-backed s3_client
@pytest.fixture(autouse=True)
def mock_s3_dependency(s3_client):
    with patch("utils.media_processors.base.s3", s3_client):
        yield

class TestImageProcessor:
    
    def test_process_large_image_pipeline(self, s3_client, media_asset):
        """
        Scenario: User uploads a High-Res (2000x2000) image.
        Expectation:
        - Main image resized to 1920x1920 (Max Dim).
        - Thumbnail created.
        - Format converted to WebP.
        - Original RAW file deleted from S3.
        """
        # 1. Setup: Create a 2000x2000 Image
        large_file = io.BytesIO()
        Image.new("RGB", (2000, 2000), color="blue").save(large_file, format="JPEG")
        large_file.seek(0)
        
        # 2. Upload "Raw" to S3
        s3_client.upload_fileobj(large_file, media_asset.bucket, media_asset.object_key)

        # 3. Action: Run Processor
        processor = ImageProcessor(media_asset)
        result = processor.process()

        # 4. Assertions
        
        # Check Dimensions (Should be downscaled to 1920)
        assert result["width"] == 1920
        assert result["height"] == 1920
        assert result["content_type"] == "image/webp"
        
        # Check Variants
        assert "thumbnail" in result["variants"]
        assert result["variants"]["original_width"] == 2000
        
        # Check Keys in S3
        s3_objects = s3_client.list_objects_v2(Bucket=media_asset.bucket)
        keys = [obj["Key"] for obj in s3_objects.get("Contents", [])]
        
        # CRITICAL: Raw file must be gone
        assert media_asset.object_key not in keys, "Raw file should be deleted"
        # New files must exist
        assert result["object_key"] in keys
        assert result["variants"]["thumbnail"] in keys

    def test_process_small_image_no_upscale(self, s3_client, media_asset):
        """
        Scenario: User uploads a Low-Res (500x500) image.
        Expectation: 
        - Image stays 500x500 (NO Upscaling).
        - Still converts to WebP.
        """
        # 1. Setup: Create a 500x500 Image
        small_file = io.BytesIO()
        Image.new("RGB", (500, 500), color="green").save(small_file, format="PNG")
        small_file.seek(0)
        
        # 2. Upload "Raw" to S3
        s3_client.upload_fileobj(small_file, media_asset.bucket, media_asset.object_key)

        # 3. Action
        processor = ImageProcessor(media_asset)
        result = processor.process()

        # 4. Assertions
        
        # Width should remain 500 (NOT upscaled to 1920)
        assert result["width"] == 500
        assert result["height"] == 500
        
        # Check Thumbnail (Should still be created, likely resized to 300)
        # Note: 500 > 300, so thumbnail will be 300.
        assert "thumbnail" in result["variants"]
        
        # Verify Raw Deletion
        s3_objects = s3_client.list_objects_v2(Bucket=media_asset.bucket)
        keys = [obj["Key"] for obj in s3_objects.get("Contents", [])]
        assert media_asset.object_key not in keys