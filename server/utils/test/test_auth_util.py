import pytest
import jwt
from auth_util import generate_otp, generate_email_token
from django.conf import settings

def test_generate_otp_format_and_length():
    otp = generate_otp()
    assert isinstance(otp, str)
    assert otp.isdigit()
    assert len(otp) == 6

def test_generate_email_token_contains_email():
    email = "testuser@example.com"
    token = generate_email_token(email)
    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    assert decoded["email"] == email
    assert "exp" in decoded
    assert "iat" in decoded

def test_token_expiry_is_about_two_minutes():
    email = "expiretest@example.com"
    token = generate_email_token(email)
    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    
    exp = decoded["exp"]
    iat = decoded["iat"]
    assert exp - iat <= 121  # slight buffer to allow test runtime
