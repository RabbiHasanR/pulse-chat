import jwt
import secrets
from datetime import datetime, timedelta, timezone
from django.conf import settings


def generate_otp() -> str:
    return str(secrets.randbelow(900000) + 100000)


def generate_email_token(email):
    now = datetime.now(timezone.utc)
    payload = {
        "email": email,
        "exp": now + timedelta(minutes=2),
        "iat": now
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')