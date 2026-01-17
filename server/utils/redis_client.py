import os
from urllib.parse import urlparse
from typing import List, Union, Dict
import redis.asyncio as redis

# --- 1. CONNECTION SETUP ---
parsed = urlparse(os.getenv("CHANNEL_URL", "redis://localhost:6379"))

redis_client = redis.Redis(
    host=parsed.hostname,
    port=parsed.port,
    db=int(parsed.path.lstrip("/")) if parsed.path else 0,
    decode_responses=True
)

# --- 2. KEY GENERATORS (DRY Pattern) ---
class RedisKeys:
    ONLINE_USERS = "online_users"

    @staticmethod
    def active_tabs(user_id: Union[int, str]) -> str:
        """Set of active tab UUIDs for a user (Online/Offline Logic)."""
        return f"user:{user_id}:online_tabs"

    @staticmethod
    def viewing(user_id: Union[int, str], target_id: Union[int, str]) -> str:
        """Set of tabs where user_id is currently viewing target_id (Read Receipt Logic)."""
        return f"user:{user_id}:viewing:{target_id}"

    @staticmethod
    def presence_audience(target_user_id: Union[int, str]) -> str:
        """Set of users listening to target_user_id's status updates (Pub/Sub Logic)."""
        return f"user:{target_user_id}:presence_audience"

# --- 3. UTILITY SERVICE (Business Logic) ---
class ChatRedisService:
    """
    Centralized logic for Chat Presence and Subscriptions.
    Use 'async_to_sync' if calling from synchronous Django Views.
    """

    @staticmethod
    async def subscribe_user_to_presence(observer_id: int, target_ids: List[int]):
        """
        Adds 'observer_id' to the audience of all 'target_ids'.
        Use this when you ONLY need to subscribe (e.g., Create New Chat).
        """
        if not target_ids:
            return

        pipeline = redis_client.pipeline()
        for target_id in target_ids:
            key = RedisKeys.presence_audience(target_id)
            pipeline.sadd(key, observer_id)
            pipeline.expire(key, 60 * 60 * 24 * 7) # 7 Days Expiry
        await pipeline.execute()

    @staticmethod
    async def get_online_status_batch(user_ids: List[int]) -> Dict[int, bool]:
        """
        Returns {user_id: True/False} for a list of users.
        Use this when you ONLY need current status (e.g., Admin Dashboard).
        """
        if not user_ids:
            return {}

        pipeline = redis_client.pipeline()
        for uid in user_ids:
            pipeline.sismember(RedisKeys.ONLINE_USERS, uid)
            
        results = await pipeline.execute()
        return {uid: bool(is_online) for uid, is_online in zip(user_ids, results)}

    @staticmethod
    async def subscribe_and_get_presences(observer_id: int, target_ids: List[int]) -> Dict[int, bool]:
        """
        OPTIMIZED: Performs Subscribing + Status Check in 1 Pipeline.
        Use this in 'ChatListView' or Scroll API to save network round-trips.
        Returns: { target_id: True/False }
        """
        if not target_ids:
            return {}

        pipeline = redis_client.pipeline()
        
        for target_id in target_ids:
            # Action 1: Subscribe
            audience_key = RedisKeys.presence_audience(target_id)
            pipeline.sadd(audience_key, observer_id)
            pipeline.expire(audience_key, 60 * 60 * 24 * 7)
            
            # Action 2: Check Status
            pipeline.sismember(RedisKeys.ONLINE_USERS, target_id)
            
        results = await pipeline.execute()
        
        status_map = {}
        # Iterate with step=3 to extract SISMEMBER results (skipping SADD/EXPIRE)
        for i, target_id in enumerate(target_ids):
            result_index = (i * 3) + 2 
            status_map[target_id] = bool(results[result_index])
            
        return status_map

    @staticmethod
    async def is_user_viewing(viewer_id: int, target_id: int) -> bool:
        """
        Checks if viewer has any active tabs looking at target.
        Use this in 'SendMessageView' for Read Receipts.
        """
        key = RedisKeys.viewing(viewer_id, target_id)
        count = await redis_client.scard(key)
        return count > 0