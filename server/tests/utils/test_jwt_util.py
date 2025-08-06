import pytest
from unittest.mock import Mock
from rest_framework_simplejwt.tokens import RefreshToken
from utils.jwt_util import (
    get_client_signature,
    issue_token_for_user,
    verify_token_signature
)


def test_get_client_signature_returns_hash(mock_request):
    request = mock_request()
    sig = get_client_signature(request)
    assert isinstance(sig, int)

def test_get_client_signature_missing_meta_fields():
    request = Mock()
    request.META = {}
    sig = get_client_signature(request)
    assert isinstance(sig, int)

@pytest.mark.django_db
def test_issue_token_contains_client_hash(user, mock_request):
    request = mock_request()
    token = issue_token_for_user(user, request)
    expected_hash = str(get_client_signature(request))

    assert token['client_hash'] == expected_hash
    assert token.access_token['client_hash'] == expected_hash

@pytest.mark.django_db
def test_verify_token_signature_valid(user, mock_request):
    request = mock_request()
    token = issue_token_for_user(user, request)
    assert verify_token_signature(token, request) is True

@pytest.mark.django_db
def test_verify_token_signature_mismatch(user, mock_request):
    request = mock_request()
    token = issue_token_for_user(user, request)

    # Alter request (simulate different client)
    request.META['REMOTE_ADDR'] = '10.0.0.1'
    assert verify_token_signature(token, request) is False

def test_verify_token_signature_missing_key(mock_request):
    request = mock_request()
    token = RefreshToken()
    assert verify_token_signature(token, request) is False
