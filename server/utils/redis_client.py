# redis_client.py
import os
from urllib.parse import urlparse
import redis.asyncio as redis

parsed = urlparse(os.getenv("CHANNEL_URL", "redis://localhost:6379"))

redis_client = redis.Redis(
    host=parsed.hostname,
    port=parsed.port,
    db=int(parsed.path.lstrip("/")) if parsed.path else 0,
    decode_responses=True
)
