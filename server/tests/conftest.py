import pytest
from users.models import ChatUser
from unittest.mock import Mock
from django.core.cache import cache

@pytest.fixture
def user(db):
    return ChatUser.objects.create_user(
        email="test@example.com",
        username="testuser",
        full_name="Test User",
        password="secret123"
    )

@pytest.fixture
def mock_request():
    request = Mock()
    request.META = {
        'REMOTE_ADDR': '192.168.1.1',
        'HTTP_USER_AGENT': 'Mozilla/5.0'
    }
    return request

@pytest.fixture
def otp():
    return "123456"

@pytest.fixture
def cache_otp(user, otp):
    cache.set(f"otp_{user.email}", otp, timeout=300)
