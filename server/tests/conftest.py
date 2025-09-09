import pytest
import asyncio
import importlib
from unittest.mock import Mock
from django.conf import settings
from django.core.cache import cache
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
    """
    Async in-memory Redis stub for the methods your consumer uses.
    """
    def __init__(self):
        self.sets = {}
        self.kv = {}
        self.expiries = {}

    # set helpers
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

    # kv helpers
    async def set(self, key, value, ex: int | None = None):
        self.kv[key] = str(value)
        if ex is not None:
            self.expiries[key] = ex

    async def get(self, key):
        val = self.kv.get(key)
        return val.encode() if val is not None else None

    async def delete(self, key):
        self.kv.pop(key, None)
        self.expiries.pop(key, None)

    async def expire(self, key, seconds):
        if key in self.kv:
            self.expiries[key] = seconds
            return True
        return False

    async def keys(self, pattern: str):
        # naive pattern support for '*' only
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
    """
    Monkeypatch the 'redis_client' imported inside your consumer module.
    """
    module_path = "channel.consumers"
    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, "redis_client", fake_redis, raising=True)
    return fake_redis