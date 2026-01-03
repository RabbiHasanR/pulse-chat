import pytest
from unittest.mock import patch
from background_worker.chats.tasks import process_uploaded_asset

@pytest.mark.django_db
class TestProcessAssetTask:

    @patch("background_worker.chats.tasks.ImageProcessor")
    @patch("background_worker.chats.tasks.notify_message_event.delay")
    def test_process_asset_success_workflow(self, mock_notify, MockProcessor, media_asset):
        """
        Scenario: ImageProcessor finishes successfully.
        Expectations:
        1. Asset status updates to 'done'.
        2. Asset metadata (width, height, variants) is saved.
        3. Message status updates to 'sent'.
        4. WebSocket notification sent with 'success=True' and signed URLs.
        """
        # 1. Setup Mock Processor to return fake results
        # We simulate that the processor did its job and returned this dict
        mock_instance = MockProcessor.return_value
        mock_instance.process.return_value = {
            "object_key": "optimized.webp",
            "content_type": "image/webp",
            "file_size": 500,
            "width": 1920,
            "height": 1080,
            "variants": {"thumbnail": "thumb.webp"}
        }

        # 2. Action: Run the Task
        process_uploaded_asset(media_asset.id)

        # 3. Verify DB Updates (Asset)
        media_asset.refresh_from_db()
        assert media_asset.processing_status == "done"
        assert media_asset.processing_progress == 1.0
        assert media_asset.width == 1920
        assert media_asset.object_key == "optimized.webp"
        
        # 4. Verify DB Updates (Message)
        msg = media_asset.message
        msg.refresh_from_db()
        assert msg.status == "sent" # Pending -> Sent

        # 5. Verify Notification Payload
        assert mock_notify.called
        # Get the argument passed to notify_message_event.delay(payload)
        payload = mock_notify.call_args[0][0]
        
        assert payload["success"] is True
        assert payload["data"]["status"] == "sent"
        assert payload["data"]["stage"] == "done"
        
        # Critical: Ensure URLs are included in the final payload
        assert "media_url" in payload["data"]
        assert "thumbnail_url" in payload["data"]

    @patch("background_worker.chats.tasks.ImageProcessor")
    @patch("background_worker.chats.tasks.notify_message_event.delay")
    def test_process_asset_failure_handling(self, mock_notify, MockProcessor, media_asset):
        """
        Scenario: ImageProcessor crashes (e.g. corrupt file).
        Expectations:
        1. Asset status updates to 'failed'.
        2. Message status remains 'pending' (or whatever logic you prefer).
        3. WebSocket notification sent with 'success=False' and error message.
        """
        # 1. Setup Mock to Raise Error
        mock_instance = MockProcessor.return_value
        mock_instance.process.side_effect = Exception("Corrupt File")

        # 2. Action
        process_uploaded_asset(media_asset.id)

        # 3. Verify DB Updates
        media_asset.refresh_from_db()
        assert media_asset.processing_status == "failed"
        
        # Message should NOT be marked as sent if processing failed
        media_asset.message.refresh_from_db()
        assert media_asset.message.status == "pending"

        # 4. Verify Error Notification
        assert mock_notify.called
        payload = mock_notify.call_args[0][0]
        
        assert payload["success"] is False
        assert payload["data"]["stage"] == "failed"
        assert "Corrupt File" in payload["data"]["error"]