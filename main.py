"""
main.py — Bot entry point.

Startup sequence:
  1. Load config and setup logging
  2. Init SQLite database
  3. Create OneClick API client and all service objects
  4. Register middleware stack:
     a. ServicesMiddleware  — injects db, config, and services
     b. RateLimitMiddleware — enforces per-user request limits
  5. Include handler router
  6. Start polling
"""

import asyncio
import logging
import sys
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

from config import Config, load_config
from dashboard import DashboardService
from distributors import DistributorRechargeService, DistributorService, DistributorWalletService
from database import Database
from error_handler import handle_global_error
from handlers import router
from ops_center import OperationsCenterService
from ratelimiter import RateLimiter, RateLimitMiddleware
from recharge_tracker import RechargeTracker
from services import (
    GamesService, GiftCardService,
    OneClickAPI, RechargeService, WalletService,
)

# Transactions still "pending"/"processing" after this many minutes are
# assumed to belong to a previous process that crashed or was restarted
# mid-flight. RechargeService never exposes a mid-flight resume point, so
# true re-attach-to-polling recovery isn't possible without touching it —
# instead, on startup we safely mark them as an ambiguous failure (never
# re-submitting to OneClick, never re-deducting the wallet) and, if the
# original tracking card is still known, edit it to say so.
STALE_INFLIGHT_GRACE_MINUTES = 5


async def _recover_stale_inflight_transactions(bot: Bot, db: Database, logger: logging.Logger) -> None:
    """
    Startup-only safety sweep — never called mid-session.

    Any transaction still "pending"/"processing" older than
    STALE_INFLIGHT_GRACE_MINUTES belonged to a process that is no longer
    running (this one just started). We mark it "failed" via the existing
    update_transaction() (same method RechargeService itself uses) so it
    stops showing as in-flight, and — if we still know its tracking card —
    edit that message to the generic "status unconfirmed" text. We never
    resubmit to OneClick and never touch the wallet balance here.
    """
    from utils import get_text  # local import to avoid a module-level cycle
    from aiogram.exceptions import TelegramAPIError

    stale = await db.get_stale_inflight_transactions(STALE_INFLIGHT_GRACE_MINUTES)
    if not stale:
        return

    logger.warning("Startup recovery: found %d stale in-flight transaction(s) to reconcile", len(stale))
    for tx in stale:
        await db.update_transaction(tx["id"], "failed", "startup_recovery: ambiguous outcome")
        chat_id = tx.get("tracking_chat_id")
        message_id = tx.get("tracking_message_id")
        if not chat_id or not message_id:
            continue
        user = await db.get_user_by_id(tx["user_id"])
        lang = user["language"] if user else "ar"
        try:
            await bot.edit_message_text(
                get_text("tracking_ambiguous", lang), chat_id=chat_id, message_id=message_id
            )
        except TelegramAPIError as exc:
            logger.error(
                "Startup recovery: failed to edit tracking card for tx_id=%s chat_id=%s error=%s",
                tx["id"], chat_id, exc,
            )


# ---------------------------------------------------------------------------
# Services injection middleware
# ---------------------------------------------------------------------------

class ServicesMiddleware(BaseMiddleware):
    """Injects all shared objects into every handler via `data`."""

    def __init__(
        self,
        db: Database,
        config: Config,
        api: OneClickAPI,
        recharge_service: RechargeService,
        games_service: GamesService,
        gift_card_service: GiftCardService,
        wallet_service: WalletService,
        dashboard_service: DashboardService,
        ops_center_service: OperationsCenterService,
        distributor_service: DistributorService,
        distributor_wallet_service: DistributorWalletService,
        distributor_recharge_service: DistributorRechargeService,
        tracker: RechargeTracker,
    ) -> None:
        self._db = db
        self._config = config
        self._api = api
        self._recharge_service = recharge_service
        self._games_service = games_service
        self._gift_card_service = gift_card_service
        self._wallet_service = wallet_service
        self._dashboard_service = dashboard_service
        self._ops_center_service = ops_center_service
        self._distributor_service = distributor_service
        self._distributor_wallet_service = distributor_wallet_service
        self._distributor_recharge_service = distributor_recharge_service
        self._tracker = tracker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["db"]                = self._db
        data["config"]            = self._config
        data["api"]               = self._api
        data["recharge_service"]  = self._recharge_service
        data["games_service"]     = self._games_service
        data["gift_card_service"] = self._gift_card_service
        data["wallet_service"]    = self._wallet_service
        data["dashboard_service"] = self._dashboard_service
        data["ops_center_service"] = self._ops_center_service
        data["distributor_service"]         = self._distributor_service
        data["distributor_wallet_service"]  = self._distributor_wallet_service
        data["distributor_recharge_service"] = self._distributor_recharge_service
        data["tracker"]                     = self._tracker
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(config: Config) -> None:
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
    except OSError as exc:
        print(f"Warning: cannot open log file: {exc}", file=sys.stderr)

    logging.basicConfig(level=log_level, format=fmt, handlers=handlers)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    config = load_config()
    setup_logging(config)

    logger = logging.getLogger(__name__)
    logger.info("Starting bot — %s", "🔧 MOCK MODE ON" if config.MOCK_MODE else "🚀 LIVE MODE")
    logger.info("Admin IDs: %s", config.ADMIN_IDS)
    logger.info(
        "Rate limit: %d req / %ds | API retries: %d | timeout: %ds",
        config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW,
        config.API_MAX_RETRIES, config.API_TIMEOUT,
    )
    if not config.MOCK_MODE and not config.ONECLICK_API_KEY:
        logger.error("ONECLICK_API_KEY is not set but MOCK_MODE=false — API calls will fail!")

    # Database
    db = Database(config.DATABASE_PATH)
    await db.init()

    # API client
    api = OneClickAPI(config)

    # Services
    recharge_service  = RechargeService(db, api)
    games_service     = GamesService(db, api)
    gift_card_service = GiftCardService(db, api)
    wallet_service    = WalletService(db)
    dashboard_service = DashboardService(db, api, config)
    ops_center_service = OperationsCenterService(db, dashboard_service, config)
    distributor_service          = DistributorService(db)
    distributor_wallet_service   = DistributorWalletService(db)
    distributor_recharge_service = DistributorRechargeService(db, api, distributor_wallet_service)

    # Bot + Dispatcher
    bot     = Bot(token=config.BOT_TOKEN,
                  default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp      = Dispatcher(storage=storage)

    tracker = RechargeTracker(bot, db)

    # Middleware — order matters: ServicesMiddleware runs first (injects db),
    # then RateLimitMiddleware (which reads db for user language).
    services_mw = ServicesMiddleware(
        db=db, config=config,
        api=api,
        recharge_service=recharge_service,
        games_service=games_service,
        gift_card_service=gift_card_service,
        wallet_service=wallet_service,
        dashboard_service=dashboard_service,
        ops_center_service=ops_center_service,
        distributor_service=distributor_service,
        distributor_wallet_service=distributor_wallet_service,
        distributor_recharge_service=distributor_recharge_service,
        tracker=tracker,
    )
    rate_limiter = RateLimiter(config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW)
    rate_mw      = RateLimitMiddleware(rate_limiter)

    for event_type in (dp.message, dp.callback_query):
        event_type.middleware(services_mw)
        event_type.middleware(rate_mw)

    # Handlers
    dp.include_router(router)

    # Recovery sweep for in-flight transactions left behind by a previous
    # process (crash / restart / deploy). We deliberately do NOT re-poll or
    # re-submit to OneClick here — RechargeService gives us no safe mid-flight
    # resume point without modifying it. Instead we mark them as an ambiguous
    # failure (no resubmission, no re-deduction) and, if we still know the
    # tracking card, let the user know via that same message.
    await _recover_stale_inflight_transactions(bot, db, logger)

    # Global error handler — last-resort safety net for exceptions that
    # escape every local try/except; never interferes with recharge/Activy/
    # OneClick/wallet flows or their own dedicated error handling.
    dp.error()(handle_global_error)

    logger.info("Bot is polling...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        logger.info("Shutting down gracefully...")
        await api.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bot stopped.")
