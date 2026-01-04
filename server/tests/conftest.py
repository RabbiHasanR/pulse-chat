import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

import json
import pytest
import asyncio
import importlib
from unittest.mock import Mock

import io
import boto3
from PIL import Image
from moto import mock_aws
from chats.models import ChatMessage, MediaAsset

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
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
def another_user(db):
    return ChatUser.objects.create_user(
        email="another@example.com",
        username="anotheruser",
        full_name="Another User",
        password="anotherpass"
    )
    
@pytest.fixture
def third_user(db):
    return ChatUser.objects.create_user(
        email="u3@example.com",
        username="u3",
        full_name="U 3",
        password="x"
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




pytest_plugins = ("pytest_asyncio",)

@pytest.fixture(autouse=True, scope="session")
def _configure_settings():
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }




class _FakeRedis:
    def __init__(self):
        self.sets: dict[str, set] = {}
        self.kv: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    # set ops
    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)
        return 1

    async def srem(self, key, member):
        if key in self.sets and member in self.sets[key]:
            self.sets[key].remove(member)
            return 1
        return 0

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    # string ops
    async def set(self, key, value, ex: int | None = None):
        self.kv[key] = str(value)
        if ex is not None:
            self.expiries[key] = ex

    async def get(self, key):
        v = self.kv.get(key)
        return v.encode() if v is not None else None

    async def delete(self, key):
        self.kv.pop(key, None)
        self.expiries.pop(key, None)

    async def expire(self, key, seconds):
        if key in self.kv:
            self.expiries[key] = seconds
            return True
        return False

    async def keys(self, pattern: str):
        # naive '*' handling: prefix match
        if "*" not in pattern:
            return [k for k in self.kv.keys() if k == pattern]
        prefix = pattern.split("*", 1)[0]
        return [k for k in self.kv.keys() if k.startswith(prefix)]

    async def mget(self, *keys):
        out = []
        for k in keys:
            v = self.kv.get(k)
            out.append(v.encode() if v is not None else None)
        return out


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def patch_redis(monkeypatch, fake_redis):
    module_path = "channel.consumers"
    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, "redis_client", fake_redis, raising=True)
    mod.UserSocketConsumer.ValidationError = ValidationError
    return fake_redis


async def recv_json(communicator, timeout=1.0):
    msg = await asyncio.wait_for(communicator.receive_from(), timeout=timeout)
    return json.loads(msg)


async def recv_until(communicator, predicate, timeout=1.5):
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            msg = await recv_json(communicator, timeout=timeout)
            last = msg
            if predicate(msg):
                return msg
        except asyncio.TimeoutError:
            break
    raise asyncio.TimeoutError(f"Message satisfying predicate not received; last={last}")

async def recv_type(comm, type_, timeout=1.5):
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        per = max(0.01, min(0.2, deadline - asyncio.get_event_loop().time()))
        try:
            raw = await asyncio.wait_for(comm.receive_from(), timeout=per)
            msg = json.loads(raw)
            last = msg
            if msg.get("type") == type_:
                return msg
        except asyncio.TimeoutError:
            # spin until overall deadline
            pass
    raise asyncio.TimeoutError(f"Did not receive type={type_}; last={last}")



@pytest.fixture
def helpers():
    return {
        "recv_json": recv_json,
        "recv_until": recv_until,
        "recv_type": recv_type,
    }
    
    
    

# --- MEDIA & AWS FIXTURES ---

@pytest.fixture
def s3_client():
    """
    Starts Moto S3, creates the test bucket, and yields the client.
    This runs for every test function so you get a clean bucket each time.
    """
    with mock_aws():
        # 1. Setup Mock S3
        s3 = boto3.client("s3", region_name="us-east-1")
        
        # 2. Create the bucket defined in your code (or default)
        bucket_name = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "test-bucket")
        s3.create_bucket(Bucket=bucket_name)
        
        yield s3

@pytest.fixture
def raw_image_file():
    """
    Creates a valid 2000x2000 Red JPEG image in memory.
    Returns: io.BytesIO stream
    """
    file_stream = io.BytesIO()
    # Create a simple red image
    image = Image.new("RGB", (2000, 2000), color="red")
    image.save(file_stream, format="JPEG")
    file_stream.seek(0) # Reset pointer so it's ready to read
    return file_stream

@pytest.fixture
def chat_message(db, user, another_user):
    """Creates a pending text message between two users"""
    return ChatMessage.objects.create(
        sender_id=user.id,
        receiver_id=another_user.id,
        message_type="text",
        content="Hello",
        status="pending"
    )

@pytest.fixture
def media_asset(db, chat_message):
    """
    Creates a MediaAsset linked to the chat_message.
    Note: object_key points to a fake file.
    """
    return MediaAsset.objects.create(
        message=chat_message,
        kind="image",
        bucket="test-bucket",
        object_key="raw_uploads/test_image.jpg",
        file_name="test_image.jpg",
        file_size=1024,
        processing_status="queued"
    )