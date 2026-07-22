"""
ratelimiter.py — In-memory, async-safe per-user rate limiter.

Uses a sliding-window algorithm: tracks timestamps of recent events
per user and rejects when the count in the window exceeds the limit.

Designed to be instantiated once and shared via middleware.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, Tuple

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from utils import get_text

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window in-memory rate limiter. Thread/task-safe via asyncio.Lock."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._history: dict[int, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, user_id: int) -> Tuple[bool, int]:
        """
        Returns (allowed, retry_after_seconds).
        retry_after is 0 when allowed, positive when rate-limited.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            history = [t for t in self._history[user_id] if t > cutoff]

            if len(history) >= self._max:
                oldest = min(history)
                retry_after = int(self._window - (now - oldest)) + 1
                self._history[user_id] = history
                return False, retry_after

            history.append(now)
            self._history[user_id] = history
            return True, 0

    async def reset(self, user_id: int) -> None:
        async with self._lock:
            self._history.pop(user_id, None)


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware that enforces per-user rate limits.

    Injects `rate_limiter` into handler data so handlers can call
    it manually for fine-grained control if needed.

    Automatically rejects requests that exceed the limit, sending
    a translated error message to the user.
    """

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["rate_limiter"] = self._limiter

        # Resolve user_id and language from event
        user_id: int | None = None
        lang = "ar"

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            db = data.get("db")
            if db:
                user = await db.get_user(user_id)
                if user:
                    lang = user.get("language", "ar")

        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
            db = data.get("db")
            if db:
                user = await db.get_user(user_id)
                if user:
                    lang = user.get("language", "ar")

        if user_id is not None:
            allowed, retry_after = await self._limiter.check(user_id)
            if not allowed:
                logger.warning("Rate limit hit: user_id=%s retry_after=%ss", user_id, retry_after)
                text = get_text("rate_limited", lang, seconds=str(retry_after))

                if isinstance(event, Message):
                    await event.answer(text)
                elif isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                return  # drop the event

        return await handler(event, data)
