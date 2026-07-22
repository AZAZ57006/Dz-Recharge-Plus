"""
error_handler.py — Global error handler (infrastructure only).

Registered once via `dp.error()` in main.py. This is the last-resort safety
net for exceptions that escape every local try/except in handlers.py and
services.py — it never replaces or changes any existing error handling
(recharge/Activy/OneClick/wallet flows keep their own dedicated try/except
blocks exactly as before).

Responsibilities (and nothing more):
  1. Classify the exception (Telegram API / OneClick API / Database /
     Validation / unexpected internal).
  2. Log the full exception with structured context (user/chat/update type)
     for debugging.
  3. Best-effort notify the user with a single generic, localized message —
     never a traceback or internal detail — sent as a NEW message (never an
     edit, since the triggering message may itself be the cause of failure).

Never raises further: a failure while handling an error (e.g. bot blocked by
the user, DB unavailable for a language lookup) is swallowed and logged, so
this module can never itself crash polling or produce a second unhandled
exception.
"""

import logging
from typing import Any, Optional

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, Update

from database import Database
from services import APIError
from utils import get_text

logger = logging.getLogger(__name__)

# Order matters: checked top to bottom, most specific first.
_CATEGORY_TELEGRAM = "telegram_api_error"
_CATEGORY_ONECLICK = "oneclick_api_error"
_CATEGORY_DATABASE = "database_error"
_CATEGORY_VALIDATION = "validation_error"
_CATEGORY_UNEXPECTED = "unexpected_internal_error"


def classify_exception(exc: BaseException) -> str:
    """
    Best-effort classification for logging/observability only — never used
    to change behavior or retry logic.
    """
    if isinstance(exc, TelegramAPIError):
        return _CATEGORY_TELEGRAM
    if isinstance(exc, APIError):
        return _CATEGORY_ONECLICK
    if isinstance(exc, (aiosqlite.Error,)):
        return _CATEGORY_DATABASE
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        # Local validation already handles these in-flow; reaching here means
        # one slipped past its local try/except — still worth flagging as
        # "validation-shaped" rather than a true internal bug.
        return _CATEGORY_VALIDATION
    return _CATEGORY_UNEXPECTED


def _extract_context(update: Update) -> dict:
    """Best-effort extraction of user_id/chat_id/update_type for logging."""
    user_id: Optional[int] = None
    chat_id: Optional[int] = None
    update_type = "unknown"
    detail = ""

    try:
        update_type = update.event_type
    except Exception:
        pass

    message = update.message or update.edited_message
    if message is not None:
        user_id = message.from_user.id if message.from_user else None
        chat_id = message.chat.id if message.chat else None
        detail = (message.text or message.caption or "")[:120]
    elif update.callback_query is not None:
        cq = update.callback_query
        user_id = cq.from_user.id if cq.from_user else None
        chat_id = cq.message.chat.id if cq.message and cq.message.chat else None
        detail = (cq.data or "")[:120]

    return {
        "user_id": user_id,
        "chat_id": chat_id,
        "update_type": update_type,
        "detail": detail,
    }


async def _safe_lang(db: Optional[Database], user_id: Optional[int]) -> str:
    """Best-effort language lookup; never raises, defaults to 'ar'."""
    if db is None or user_id is None:
        return "ar"
    try:
        user = await db.get_user(user_id)
        return user["language"] if user else "ar"
    except Exception:
        return "ar"


async def handle_global_error(event: ErrorEvent, bot: Optional[Bot] = None, db: Optional[Database] = None, **_: Any) -> bool:
    """
    Registered via `dp.error()`. Must never raise — this is the last line of
    defense before aiogram would otherwise just log-and-drop the update.
    """
    exc = event.exception
    ctx = _extract_context(event.update)
    category = classify_exception(exc)

    logger.exception(
        "Unhandled exception | category=%s | user_id=%s | chat_id=%s | "
        "update_type=%s | detail=%r",
        category, ctx["user_id"], ctx["chat_id"], ctx["update_type"], ctx["detail"],
        exc_info=exc,
    )

    if bot is not None and ctx["chat_id"] is not None:
        try:
            lang = await _safe_lang(db, ctx["user_id"])
            await bot.send_message(ctx["chat_id"], get_text("error_unhandled_generic", lang))
        except Exception as notify_exc:
            logger.error("Global error handler could not notify user: %s", notify_exc)

    # Mark the update as handled so aiogram doesn't re-raise/propagate further
    # and polling continues uninterrupted for the next update.
    return True
