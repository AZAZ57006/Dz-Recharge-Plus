"""
recharge_tracker.py — Generic asynchronous "confirm -> background provider
call -> single tracking-card edit" helper.

RechargeTracker is deliberately generic so any future money-moving,
confirm-style flow (Games, Gift Cards, wallet withdrawals) can reuse it
without duplicating this plumbing. It never talks to OneClick, wallet
balances, or RechargeService itself — callers pass in the exact
(unmodified) service coroutine to await plus render callbacks that turn
its result dict into (text, reply_markup) for the final card.

Responsibilities (and only these):
  - Spawn the tracked coroutine as a background asyncio.Task so the
    confirm handler can return immediately after showing the tracking
    card (the "instant confirm" UX).
  - Attach the tracking message (chat_id/message_id) to the transaction
    row RechargeService just created, via the new, additive
    tracking_chat_id/tracking_message_id columns — used for the startup
    recovery sweep in main.py.
  - Edit that one message from "Processing..." to the final
    success/failure card once the tracked coroutine resolves.
  - Provide its own safety net (separate from error_handler.py) so an
    unexpected exception in the tracked coroutine still resolves the
    card instead of leaving the user staring at "Processing..." forever.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from database import Database
from keyboards import tracking_keyboard
from utils import get_text

logger = logging.getLogger(__name__)

RenderFn = Callable[[Dict[str, Any]], Tuple[str, Optional[InlineKeyboardMarkup]]]

# How long to keep polling for the transaction row that RechargeService's
# process_* methods create as their very first step, before giving up on
# attaching the tracking card to it. In practice this resolves in a single
# loop iteration since that row is created before any network I/O.
_ATTACH_ATTEMPTS = 30
_ATTACH_INTERVAL_SECONDS = 0.1


class RechargeTracker:
    def __init__(self, bot: Bot, db: Database) -> None:
        self._bot = bot
        self._db = db

    def track(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        phone: str,
        lang: str,
        coro: Awaitable[Dict[str, Any]],
        render_success: RenderFn,
        render_failure: RenderFn,
    ) -> None:
        """Fire-and-forget: spawns the background task and returns immediately."""
        asyncio.create_task(
            self._run(chat_id, message_id, user_id, phone, lang, coro, render_success, render_failure)
        )

    async def _attach_tx(
        self, user_id: int, phone: str, chat_id: int, message_id: int, lang: str
    ) -> Optional[int]:
        for _ in range(_ATTACH_ATTEMPTS):
            tx_id = await self._db.get_latest_inflight_transaction_id(user_id, phone)
            if tx_id:
                await self._db.set_tracking_message(tx_id, chat_id, message_id)
                # Add the optional "Check Now" button now that we know the
                # tx_id it needs to reference. Best-effort only — if this
                # edit fails (e.g. "message not modified"), the tracking
                # card still works, it just won't show the button.
                try:
                    await self._bot.edit_message_text(
                        get_text("tracking_submitted", lang),
                        chat_id=chat_id,
                        message_id=message_id,
                        reply_markup=tracking_keyboard(tx_id, lang),
                    )
                except TelegramAPIError:
                    pass
                return tx_id
            await asyncio.sleep(_ATTACH_INTERVAL_SECONDS)
        logger.error(
            "RechargeTracker: could not attach tracking card to a transaction row — "
            "user_id=%s phone=%s chat_id=%s message_id=%s",
            user_id, phone, chat_id, message_id,
        )
        return None

    async def _run(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        phone: str,
        lang: str,
        coro: Awaitable[Dict[str, Any]],
        render_success: RenderFn,
        render_failure: RenderFn,
    ) -> None:
        # `coro` is what actually creates the transaction row (inside
        # RechargeService.process_*), so it must be started immediately —
        # not awaited after _attach_tx, or attach_tx polls for a row that
        # will never appear in its window and the Check Now button never
        # shows up. Run both concurrently: the tracked call proceeds at
        # full speed, while we separately poll for its tx row to attach
        # the tracking card to.
        work_task = asyncio.create_task(coro)
        attach_task = asyncio.create_task(
            self._attach_tx(user_id, phone, chat_id, message_id, lang)
        )

        try:
            result = await work_task
        except Exception:
            logger.exception(
                "RechargeTracker: unhandled exception in tracked coroutine — "
                "user_id=%s phone=%s chat_id=%s message_id=%s",
                user_id, phone, chat_id, message_id,
            )
            await self._settle(attach_task, chat_id, message_id, get_text("error_unhandled_generic", lang), None)
            return

        try:
            if result.get("success"):
                text, kb = render_success(result)
            else:
                text, kb = render_failure(result)
        except Exception:
            logger.exception(
                "RechargeTracker: render callback raised — user_id=%s phone=%s chat_id=%s message_id=%s",
                user_id, phone, chat_id, message_id,
            )
            text, kb = get_text("error_unhandled_generic", lang), None

        await self._settle(attach_task, chat_id, message_id, text, kb)

    async def _settle(
        self,
        attach_task: "asyncio.Task[Optional[int]]",
        chat_id: int,
        message_id: int,
        text: str,
        kb: Optional[InlineKeyboardMarkup],
    ) -> None:
        """
        The tracked work can finish before the (separately-scheduled) attach
        task gets a chance to edit in its "Check Now" state — in that rare
        fast-path race, we must not let attach's edit land *after* the final
        card and stomp it back to "submitted". So: if the work already
        finished first, cancel the attach task (or, if its edit already
        landed, our final edit below simply overwrites it, which is fine).
        """
        if not attach_task.done():
            attach_task.cancel()
        try:
            # Either awaits the cancellation to completion, or (if it was
            # already done) lets any already-in-flight attach edit settle
            # before we send the definitive one, so ours is always last.
            await attach_task
        except (asyncio.CancelledError, Exception):
            pass
        await self._finish(chat_id, message_id, text, kb)

    async def _finish(
        self, chat_id: int, message_id: int, text: str, kb: Optional[InlineKeyboardMarkup]
    ) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=kb
            )
        except TelegramAPIError as exc:
            logger.error(
                "RechargeTracker: failed to edit tracking message — chat_id=%s message_id=%s error=%s",
                chat_id, message_id, exc,
            )
