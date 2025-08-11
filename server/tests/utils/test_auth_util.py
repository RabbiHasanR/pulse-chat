import jwt
import pytest
from utils.auth_util import generate_otp, generate_email_token
from django.conf import settings

@pytest.fixture
def decoded_email_token():
    def _decode(email="testuser@example.com"):
        token = generate_email_token(email)
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return decoded
    return _decode

def test_generate_otp_format_and_length():
    otp = generate_otp()
    assert isinstance(otp, str)
    assert otp.isdigit()
    assert len(otp) == 6

def test_generate_email_token_contains_email(decoded_email_token):
    email = "testuser@example.com"
    decoded = decoded_email_token(email)
    assert decoded["email"] == email
    assert "exp" in decoded
    assert "iat" in decoded

def test_token_expiry_is_about_two_minutes(decoded_email_token):
    decoded = decoded_email_token("expiretest@example.com")
    exp = decoded["exp"]
    iat = decoded["iat"]
    assert exp - iat <= 121
