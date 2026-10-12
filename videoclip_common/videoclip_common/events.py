"""
Redis pub/sub event system for async inter-service communication.
Publish side-effect events (meeting_completed, team_invite_created, etc.)
that other services consume asynchronously.
"""

import json
import asyncio
from typing import Any, Callable, Awaitable

import redis.asyncio as aioredis


class RedisEventPublisher:
    """Publishes events to Redis channels."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """Publish an event to a Redis channel."""
        r = await self._get_redis()
        await r.publish(channel, json.dumps(event))

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None


class RedisEventConsumer:
    """Subscribes to Redis channels and dispatches events to handlers."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._redis: aioredis.Redis | None = None
        self._task: asyncio.Task | None = None

    def subscribe(self, channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        """Register a handler for a channel. Call before start()."""
        self._handlers[channel] = handler

    async def start(self) -> None:
        """Start listening for events in a background task."""
        if not self._handlers:
            return
        self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(*self._handlers.keys())
        self._task = asyncio.create_task(self._listen(pubsub))

    async def _listen(self, pubsub) -> None:
        """Internal listener loop."""
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            handler = self._handlers.get(channel)
            if handler:
                try:
                    data = json.loads(message["data"])
                    await handler(data)
                except Exception:
                    pass  # Services should add their own logging

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.close()
            self._redis = None
