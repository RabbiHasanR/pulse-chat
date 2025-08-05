import pytest
from unittest.mock import Mock
from rest_framework_simplejwt.tokens import RefreshToken
from users.models import ChatUser
from ..jwt_util import (
    get_client_signature,
    issue_token_for_user,
    verify_token_signature
)

@pytest.fixture
def real_user(db):
    return ChatUser.objects.create_user(
        email='test@example.com',
        username='testuser',
        full_name='Test User',
        password='testpass123'
    )

@pytest.fixture
def mock_request():
    request = Mock()
    request.META = {
        'REMOTE_ADDR': '192.168.1.1',
        'HTTP_USER_AGENT': 'Mozilla/5.0'
    }
    return request

def test_get_client_signature_returns_hash(mock_request):
    sig = get_client_signature(mock_request)
    assert isinstance(sig, int)

def test_get_client_signature_missing_meta_fields():
    request = Mock()
    request.META = {}
    sig = get_client_signature(request)
    assert isinstance(sig, int)

@pytest.mark.django_db
def test_issue_token_contains_client_hash(real_user, mock_request):
    token = issue_token_for_user(real_user, mock_request)
    expected_hash = str(get_client_signature(mock_request))

    assert token['client_hash'] == expected_hash
    assert token.access_token['client_hash'] == expected_hash

@pytest.mark.django_db
def test_verify_token_signature_valid(real_user, mock_request):
    token = issue_token_for_user(real_user, mock_request)
    assert verify_token_signature(token, mock_request) is True

@pytest.mark.django_db
def test_verify_token_signature_mismatch(real_user, mock_request):
    token = issue_token_for_user(real_user, mock_request)

    # Alter request (simulate different client)
    mock_request.META['REMOTE_ADDR'] = '10.0.0.1'
    assert verify_token_signature(token, mock_request) is False

def test_verify_token_signature_missing_key(mock_request):
    token = RefreshToken()
    assert verify_token_signature(token, mock_request) is False
