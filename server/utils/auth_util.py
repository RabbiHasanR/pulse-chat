import jwt
import random
from datetime import datetime, timedelta, timezone
from django.conf import settings

def generate_otp():
    return str(random.randint(100000, 999999))


def generate_email_token(email):
    now = datetime.now(timezone.utc)
    payload = {
        "email": email,
        "exp": now + timedelta(minutes=2),
        "iat": now
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')