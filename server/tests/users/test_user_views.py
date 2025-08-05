import jwt
import pytest
from django.conf import settings
from django.core.cache import cache

from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from users.models import ChatUser
from utils.auth_util import generate_email_token
from utils.jwt_util import (
    issue_token_for_user,
)
from tests.constants import *

# RegisterUserView Test Cases

@pytest.mark.django_db
def test_register_user_success():
    client = APIClient()
    data = {
        "email": DUMMY_EMAIL,
        "username": DUMMY_USERNAME,
        "full_name": DUMMY_NAME,
        "password": DUMMY_PASSWORD,
    }
    response = client.post(REGISTER_URL, data)
    assert response.status_code == 201
    assert response.data["message"] == "User registered"

@pytest.mark.django_db
def test_register_user_invalid_data():
    client = APIClient()
    response = client.post(REGISTER_URL, {"email": "invalid"})
    assert response.status_code == 400
    assert "username" in response.data
    assert "full_name" in response.data
    

# SendOTPView Test Cases

@pytest.mark.django_db
def test_send_otp_success(user):
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": user.email})
    assert response.status_code == 200
    assert "token" in response.data
    assert cache.get(f"otp_{user.email}") is not None

@pytest.mark.django_db
def test_send_otp_user_not_found():
    client = APIClient()
    response = client.post(SEND_OTP_URL, {"email": "notfound@example.com"})
    assert response.status_code == 404
    assert response.data["error"] == "User not found"
    

# VerifyOTPView Test Cases

@pytest.mark.django_db
def test_verify_otp_success(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": otp})
    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data

@pytest.mark.django_db
def test_verify_otp_invalid_otp(user, otp, cache_otp):
    token = generate_email_token(user.email)
    client = APIClient()
    response = client.post(VERIFY_OTP_URL, {"token": token, "otp": "000000"})
    assert response.status_code == 400
    assert response.data["error"] == "Invalid OTP"

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
    assert response.data["error"] == "Token expired"



# CustomTokenRefreshView Test Cases

class MockRequest:
    def __init__(self, ip="192.168.1.1", ua="Mozilla/5.0"):
        self.META = {
            "REMOTE_ADDR": ip,
            "HTTP_USER_AGENT": ua,
        }
        self.ip = ip
        self.ua = ua

@pytest.mark.django_db
def test_refresh_token_success():
    user = ChatUser.objects.create_user(
        email=DUMMY_EMAIL,
        username="refresher",
        full_name=DUMMY_NAME,
        password=DUMMY_PASSWORD
    )
    token = issue_token_for_user(user, MockRequest())
    print("test_refresh_token_success:", token)
    client = APIClient()
    response = client.post(REFRESH_URL, {"refresh": str(token)})
    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data

@pytest.mark.django_db
def test_refresh_token_missing():
    client = APIClient()
    response = client.post(REFRESH_URL, {})
    assert response.status_code == 400
    assert response.data["error"] == "Missing refresh token"

@pytest.mark.django_db
def test_refresh_token_client_mismatch():
    user = ChatUser.objects.create_user(
        email="mismatch@example.com",
        username="mismatch",
        full_name=DUMMY_NAME,
        password=DUMMY_PASSWORD
    )
    token = issue_token_for_user(user, MockRequest("192.168.1.1", "UA"))
    altered_request = MockRequest("10.0.0.1", "UA")

    client = APIClient()
    response = client.post(REFRESH_URL, {"refresh": str(token)}, REMOTE_ADDR=altered_request.ip, HTTP_USER_AGENT=altered_request.ua)
    assert response.status_code == 403
    assert response.data["detail"] == "Client mismatch"

@pytest.mark.django_db
def test_refresh_token_blacklisted():
    user = ChatUser.objects.create_user(
        email="black@example.com",
        username="black",
        full_name=DUMMY_NAME,
        password=DUMMY_PASSWORD
    )
    token = issue_token_for_user(user, MockRequest())
    outstanding = OutstandingToken.objects.create(
        user=user,
        jti=token["jti"],
        token=str(token),
        created_at=token["iat"],
        expires_at=token["exp"]
    )

    BlacklistedToken.objects.create(token=outstanding)

    client = APIClient()
    response = client.post(REFRESH_URL, {"refresh": str(token)})
    assert response.status_code == 401
    assert response.data["error"] == "Token is blacklisted"