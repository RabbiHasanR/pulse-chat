import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

import json
import pytest
import asyncio
import importlib
from unittest.mock import Mock
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
    async def _pred(m): return m.get("type") == type_
    return await recv_until(comm, _pred, timeout=timeout)


@pytest.fixture
def helpers():
    return {
        "recv_json": recv_json,
        "recv_until": recv_until,
        "recv_type": recv_type,
    }