import pytest
from unittest.mock import patch
from tests.constants import *
from chats.models import ChatMessage, MediaAsset


@pytest.mark.django_db
class TestUploadViews:

    def test_prepare_upload_direct_success(self, auth_client, user, another_user):
        """
        Scenario: User requests upload for a small file (Direct Mode).
        """
        payload = {
            "file_name": "test.jpg",
            "file_size": 1024, 
            "content_type": "image/jpeg",
            "message_type": "image",
            "receiver_id": another_user.id
        }

        # FIX 1: Mock the notification task (prevents 500 error from Redis connection failure)
        # FIX 2: Patch 'chats.views.s3' (ensures View uses our mock S3)
        with patch("chats.views.s3") as mock_s3, \
             patch("chats.views.notify_message_event.delay") as mock_notify:
            
            mock_s3.generate_presigned_url.return_value = "https://s3-fake-url..."
            
            # FIX 3: Use format='json' (Ensure types like int stay int)
            response = auth_client.post(PREPARE_URL, payload, format='json')

            # Debugging
            if response.status_code != 201:
                print("\nERROR DETAIL:", response.json()) 

        assert response.status_code == 201
        res = response.json()
        
        assert res["success"] is True
        assert res["data"]["mode"] == "direct"
        
        # Verify DB
        assert ChatMessage.objects.count() == 1
        assert MediaAsset.objects.count() == 1
        # Verify Notification was sent
        mock_notify.assert_called_once()

    def test_prepare_upload_multipart_success(self, auth_client, user, another_user):
        """
        Scenario: User requests upload for a LARGE file (Multipart Mode).
        """
        payload = {
            "file_name": "movie.mp4",
            "file_size": 10 * 1024 * 1024, 
            "content_type": "video/mp4",
            "message_type": "video",
            "receiver_id": another_user.id,
            "client_part_size": 5 * 1024 * 1024,
            "client_num_parts": 2
        }

        # FIX 1: Mock notification task here too
        with patch("chats.views.s3") as mock_s3, \
             patch("chats.views.notify_message_event.delay") as mock_notify:
            
            mock_s3.create_multipart_upload.return_value = {"UploadId": "test_upload_id_123"}
            mock_s3.generate_presigned_url.return_value = "https://part-url..."
            
            # FIX 2: Use format='json'
            response = auth_client.post(PREPARE_URL, payload, format='json')

            if response.status_code != 201:
                print("DEBUG ERROR:", response.json())

        assert response.status_code == 201
        data = response.json()["data"]
        
        assert data["mode"] == "multipart"
        assert data["upload_id"] == "test_upload_id_123"

    def test_complete_upload_triggers_worker(self, auth_client, media_asset):
        """
        Scenario: Client finishes upload and calls 'complete'.
        """
        payload = {
            "object_key": media_asset.object_key,
            "upload_id": "dummy_id",
            "parts": [{"ETag": "123", "PartNumber": 1}]
        }

        with patch("chats.views.process_uploaded_asset.delay") as mock_task, \
             patch("chats.views.notify_message_event.delay") as mock_notify, \
             patch("chats.views.s3"):
            
            # FIX: Use format='json' (Solves "Expected dict got str" error)
            response = auth_client.post(COMPLETE_URL, payload, format='json')

            if response.status_code != 200:
                print("DEBUG ERROR:", response.json())

        assert response.status_code == 200
        assert response.json()["success"] is True

        mock_task.assert_called_once_with(media_asset.id)
        mock_notify.assert_called_once()
        
    def test_prepare_upload_requires_auth(self, client):
        response = client.post(PREPARE_URL, {}, format='json')
        assert response.status_code == 401