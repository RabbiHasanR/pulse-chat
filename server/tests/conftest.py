import pytest
from unittest.mock import Mock
from django.core.cache import cache
from rest_framework.test import APIClient
from users.models import ChatUser
from utils.jwt_util import issue_token_for_user
from tests.constants import *

@pytest.fixture
def user(db):
    return ChatUser.objects.create_user(
        email=DUMMY_EMAIL,
        username=DUMMY_USERNAME,
        full_name=DUMMY_NAME,
        password=DUMMY_PASSWORD
    )

@pytest.fixture
def otp():
    return "123456"

@pytest.fixture
def cache_otp(user, otp):
    cache.set(f"otp_{user.email}", otp, timeout=300)

@pytest.fixture
def mock_request():
    def _make(ip="192.168.1.1", ua="Mozilla/5.0"):
        request = Mock()
        request.META = {
            'REMOTE_ADDR': ip,
            'HTTP_USER_AGENT': ua
        }
        return request
    return _make

@pytest.fixture
def issue_bound_token(user, mock_request):
    token = issue_token_for_user(user, mock_request())
    return token

@pytest.fixture
def auth_client(issue_bound_token, mock_request):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(issue_bound_token.access_token)}")
    client.defaults.update({
        "REMOTE_ADDR": mock_request().META.get('REMOTE_ADDR', ''),
        "HTTP_USER_AGENT": mock_request().META.get('HTTP_USER_AGENT', '')
    })
    return client


@pytest.fixture
def get_response():
    return lambda request: Mock(status_code=200)




