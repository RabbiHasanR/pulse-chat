import pytest
import jwt
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from tests.constants import *
from utils.auth_util import generate_email_token
from utils.jwt_util import issue_token_for_user

# --- RegisterUserView Tests ---

@pytest.mark.django_db
def test_register_user_success():
    client = APIClient()
    response = client.post(REGISTER_URL, {
        "email": DUMMY_EMAIL,
        "username": DUMMY_USERNAME,
        "full_name": DUMMY_NAME,
        "password": DUMMY_PASSWORD,
    })
    assert response.status_code == 201
    assert response.data["success"] is True
    assert response.data["message"] == "User registered"

@pytest.mark.django_db
def test_register_user_invalid_data():
    client = APIClient()
    response = client.post(REGISTER_URL, {"email": "invalid"})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert "email" in response.data["errors"]
    assert "username" in response.data["errors"]
    assert "full_name" in response.data["errors"]

# --- SendOTPView Tests ---

@pytest.mark.django_db
def test_send_otp_success(user):
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": user.email})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "token" in response.data["data"]
    assert cache.get(f"otp_{user.email}") is not None

@pytest.mark.django_db
def test_send_otp_user_not_found():
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": "notfound@example.com"})
    assert response.status_code == 404
    assert response.data["success"] is False
    assert response.data["message"] == "User not found"

# --- VerifyOTPView Tests ---

@pytest.mark.django_db
def test_verify_otp_success(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": otp})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "access" in response.data["data"]
    assert "refresh" in response.data["data"]

@pytest.mark.django_db
def test_verify_otp_invalid_otp(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": "000000"})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert response.data["message"] == "Invalid OTP"

@pytest.mark.django_db
def test_verify_otp_expired_token():
    expired_token = jwt.encode(
        {"email": DUMMY_EMAIL, "exp": 0},
        settings.SECRET_KEY,
        algorithm="HS256"
    )
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": expired_token, "otp": "123456"})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert response.data["message"] == "Token expired"

# --- CustomTokenRefreshView Tests ---

@pytest.mark.django_db
def test_refresh_token_success(issue_bound_token, mock_request):
    client = APIClient()
    ip = mock_request().META.get('REMOTE_ADDR', '')
    ua = mock_request().META.get('HTTP_USER_AGENT', '')
    response = client.post(
        REFRESH_URL,
        {"refresh": str(issue_bound_token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 200
    assert response.data["success"] is True
    assert "access" in response.data["data"]
    assert "refresh" in response.data["data"]

@pytest.mark.django_db
def test_refresh_token_missing():
    client = APIClient()
    response = client.post(REFRESH_URL, {})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert response.data["message"] == "Missing refresh token"

@pytest.mark.django_db
def test_refresh_token_client_mismatch(user, mock_request):
    original = mock_request("192.168.1.1", "UA")
    altered = mock_request("10.0.0.1", "UA")
    token = issue_token_for_user(user, original)
    client = APIClient()
    ip = altered.META.get('REMOTE_ADDR', '')
    ua = altered.META.get('HTTP_USER_AGENT', '')
    response = client.post(
        REFRESH_URL,
        {"refresh": str(token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 403
    assert response.data["success"] is False
    assert response.data["message"] == "Client mismatch"

@pytest.mark.django_db
def test_refresh_token_blacklisted(issue_bound_token):
    issue_bound_token.blacklist()
    client = APIClient()
    response = client.post(REFRESH_URL, {"refresh": str(issue_bound_token)})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert response.data["message"] == "Invalid token"

# --- LogoutView Tests ---

@pytest.mark.django_db
def test_logout_success(auth_client, issue_bound_token):
    response = auth_client.post(LOGOUT_URL, {"refresh": str(issue_bound_token)})
    assert response.status_code == 200
    assert response.data["success"] is True
    assert response.data["message"] == "Logout successful"
    assert BlacklistedToken.objects.filter(token__jti=issue_bound_token["jti"]).exists()

@pytest.mark.django_db
def test_logout_missing_token(auth_client):
    response = auth_client.post(LOGOUT_URL, {})
    assert response.status_code == 400
    assert response.data["success"] is False
    assert "refresh" in response.data["errors"]

@pytest.mark.django_db
def test_logout_invalid_token(auth_client):
    response = auth_client.post(LOGOUT_URL, {"refresh": "malformed.token.string"})
    assert response.status_code == 401
    assert response.data["success"] is False
    assert "refresh" in response.data["errors"]

@pytest.mark.django_db
def test_logout_client_mismatch(user, mock_request):
    original = mock_request("192.168.1.1", "UA")
    altered = mock_request("10.0.0.1", "UA")
    token = issue_token_for_user(user, original)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(token.access_token)}")
    ip = altered.META.get('REMOTE_ADDR', '')
    ua = altered.META.get('HTTP_USER_AGENT', '')
    response = client.post(
        LOGOUT_URL,
        {"refresh": str(token)},
        REMOTE_ADDR=ip,
        HTTP_USER_AGENT=ua
    )
    assert response.status_code == 403
    assert response.data["success"] is False
    assert response.data["errors"]["token"][0] == "Token does not match client signature"
