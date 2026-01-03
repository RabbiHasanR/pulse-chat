import pytest

@pytest.mark.django_db
class TestMediaAssetUrls:
    
    def test_thumbnail_url_uses_variant_if_present(self, media_asset):
        """
        Scenario: Processing is done, and a 'thumbnail' key exists in variants.
        Expectation: Returns the signed URL for the thumbnail file.
        """
        media_asset.variants = {"thumbnail": "thumb_123.webp"}
        media_asset.save()
        
        url = media_asset.thumbnail_url
        assert url is not None
        assert "thumb_123.webp" in url

    def test_thumbnail_url_fallback_for_images(self, media_asset):
        """
        Scenario: Image is uploaded but not processed yet (or processing failed).
        Expectation: Should return the MAIN URL (original/optimized) so the UI 
        shows the full image instead of a broken icon.
        """
        media_asset.kind = "image"
        media_asset.variants = {} # No thumbnail yet
        media_asset.save()
        
        # Should fallback to the main object key
        assert media_asset.thumbnail_url == media_asset.url
        assert media_asset.object_key in media_asset.thumbnail_url

    def test_thumbnail_url_returns_none_for_unprocessed_video(self, media_asset):
        """
        Scenario: Video uploaded but not processed.
        Expectation: Should return None. We can't display a raw video file 
        as an <img> tag in the chat list.
        """
        media_asset.kind = "video"
        media_asset.variants = {}
        media_asset.save()
        
        assert media_asset.thumbnail_url is None

    def test_main_url_generation(self, media_asset):
        """
        Scenario: Basic check to ensure the main .url property generates 
        a valid signed link to the current object_key.
        """
        media_asset.object_key = "final_optimized.webp"
        media_asset.save()
        
        url = media_asset.url
        assert url is not None
        assert "final_optimized.webp" in url
        # Ensure it's a signed URL (contains signature params)
        assert "Signature=" in url or "X-Amz-Signature=" in url