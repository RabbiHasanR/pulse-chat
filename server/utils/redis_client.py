import os
from urllib.parse import urlparse
import redis.asyncio as async_redis  # Rename for clarity
import redis as sync_redis           # <--- ADD THIS (Standard synchronous lib)

# --- CONFIG ---
redis_url = os.getenv("CHANNEL_URL", "redis://localhost:6379")
parsed = urlparse(redis_url)

redis_host = parsed.hostname
redis_port = parsed.port
redis_db = int(parsed.path.lstrip("/")) if parsed.path else 0

# --- 1. ASYNC CLIENT (For Views/Consumers) ---
redis_client = async_redis.Redis(
    host=redis_host,
    port=redis_port,
    db=redis_db,
    decode_responses=True
)

# --- 2. SYNC CLIENT (For Celery Tasks) ---
sync_redis_client = sync_redis.Redis(
    host=redis_host,
    port=redis_port,
    db=redis_db,
    decode_responses=True
)

class RedisKeys:
    ONLINE_USERS = "online_users"

    @staticmethod
    def active_connections(user_id):
        return f"user:{user_id}:connections"

    @staticmethod
    def viewing(user_id, target_id):
        return f"user:{user_id}:viewing:{target_id}"

    @staticmethod
    def presence_audience(target_user_id):
        return f"user:{target_user_id}:presence_audience"

# --- 3. UTILITY SERVICE ---
class ChatRedisService:
    """
    Centralized logic for Chat Presence and Subscriptions.
    """

    @staticmethod
    async def subscribe_user_to_presence(observer_id: int, target_ids: list[int]):
        if not target_ids: return
        pipeline = redis_client.pipeline()
        for target_id in target_ids:
            key = RedisKeys.presence_audience(target_id)
            pipeline.sadd(key, observer_id)
            pipeline.expire(key, 60 * 60 * 24 * 7)
        await pipeline.execute()

    @staticmethod
    async def get_online_status_batch(user_ids: list[int]) -> dict[int, bool]:
        if not user_ids: return {}
        pipeline = redis_client.pipeline()
        for uid in user_ids:
            pipeline.sismember(RedisKeys.ONLINE_USERS, uid)
        results = await pipeline.execute()
        return {uid: bool(is_online) for uid, is_online in zip(user_ids, results)}

    @staticmethod
    async def subscribe_and_get_presences(observer_id: int, target_ids: list[int]) -> dict[int, bool]:
        if not target_ids: return {}
        pipeline = redis_client.pipeline()
        for target_id in target_ids:
            # 1. Subscribe
            audience_key = RedisKeys.presence_audience(target_id)
            pipeline.sadd(audience_key, observer_id)
            pipeline.expire(audience_key, 60 * 60 * 24 * 7)
            # 2. Check Status
            pipeline.sismember(RedisKeys.ONLINE_USERS, target_id)
        results = await pipeline.execute()
        
        status_map = {}
        # Iterate with step=3 (SADD, EXPIRE, SISMEMBER)
        for i, target_id in enumerate(target_ids):
            result_index = (i * 3) + 2 
            status_map[target_id] = bool(results[result_index])
        return status_map

    @staticmethod
    async def is_user_viewing(viewer_id: int, target_id: int) -> bool:
        """Checks if ANY of the user's active sockets are viewing the target."""
        key = RedisKeys.viewing(viewer_id, target_id)
        count = await redis_client.scard(key)
        return count > 0