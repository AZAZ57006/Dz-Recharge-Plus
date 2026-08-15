"""
handlers.py — All aiogram 3.x routers and message/callback handlers.

Sections:
  1. Helpers
  2. /start, /help, /balance, /history, /language, /admin
  3. Text input handler (recharge parsing with operator detection)
  4. Menu callbacks
  5. Standard recharge confirm
  6. Activy callbacks
  7. Games callbacks
  8. Gift card callbacks
  9. Language callback
  10. Wallet / deposit callbacks
  11. Admin callbacks
  12. Admin deposit callbacks
  13. Admin FSM helpers
"""

import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram import Router, F, Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from database import Database
from keyboards import (
    AdminCallback, AdminDepositCallback,
    ActivyCallback, ActivyNavCallback, ActivyOperatorCallback, ConfirmCallback,
    DashboardCallback,
    DepositCallback,
    DistributorActivyCallback, DistributorActivyNavCallback, DistributorActivyOperatorCallback,
    DistributorAdminCallback,
    DistributorConfirmCallback, DistributorStdOperatorCallback,
    DistributorWalletCallback,
    FavoriteActionCallback, FavoriteMenuCallback, FavoriteSelectCallback,
    GameConfirmCallback, GameSelectCallback,
    GiftConfirmCallback, GiftSelectCallback,
    HistoryActionCallback, HistoryNavCallback, HistorySelectCallback,
    LangCallback, MenuCallback, StdOperatorCallback, CheckNowCallback,
    OpsCallback,
    activy_confirm_keyboard, activy_offers_keyboard, activy_operator_choice_keyboard,
    admin_deposit_action_keyboard, admin_keyboard,
    admin_user_actions_keyboard,
    confirm_recharge_keyboard, std_operator_choice_keyboard,
    dashboard_alerts_keyboard, dashboard_diagnostics_keyboard,
    dashboard_home_keyboard, dashboard_stats_keyboard,
    distributor_admin_menu_keyboard, distributor_detail_keyboard, distributor_list_keyboard,
    distributor_activy_offers_keyboard, distributor_activy_operator_choice_keyboard,
    distributor_activy_confirm_keyboard, distributor_activy_failure_keyboard,
    distributor_confirm_recharge_keyboard, distributor_recharge_failure_keyboard,
    distributor_std_operator_choice_keyboard,
    distributor_wallet_menu_keyboard, distributor_wallet_confirm_keyboard,
    distributor_ledger_keyboard, distributor_ledger_entry_keyboard,
    distributor_reply_keyboard,
    dist_self_wallet_keyboard, dist_self_ledger_keyboard, dist_self_ledger_entry_keyboard,
    DistributorSelfCallback,
    dist_preview_pick_keyboard, dist_preview_wallet_keyboard,
    dist_preview_ledger_keyboard, dist_preview_entry_keyboard,
    DistPreviewCallback,
    ops_center_home_keyboard, ops_list_keyboard, ops_tx_detail_keyboard,
    deposit_amounts_keyboard, deposit_confirm_keyboard,
    favorite_actions_keyboard, favorite_delete_confirm_keyboard, favorites_list_keyboard,
    game_confirm_keyboard, game_packages_keyboard,
    games_menu_keyboard,
    help_keyboard,
    gift_amounts_keyboard, gift_confirm_keyboard, gift_cards_menu_keyboard,
    history_details_keyboard, history_filter_keyboard, history_list_keyboard,
    language_keyboard,
    recharge_failure_keyboard, activy_failure_keyboard,
    tracking_keyboard,
    utility_reply_keyboard,
    wallet_keyboard,
)
from dashboard import DashboardService
from distributors import (
    DistributorRechargeService, DistributorService, DistributorWalletService,
    resolve_role, ROLE_DISTRIBUTOR,
)
from ops_center import OperationsCenterService
from recharge_tracker import RechargeTracker
from services import (
    GAMES, GIFT_CARDS,
    GamesService, GiftCardService, OneClickAPI, OperatorDetector,
    RechargeService, WalletService,
)
from utils import (
    TEXTS,
    format_amount, format_amount_ledger, format_datetime, get_text,
    is_algerian_phone, parse_recharge_input,
    tx_status_label, tx_type_label,
)

logger = logging.getLogger(__name__)
router = Router()


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_add_balance_id     = State()
    waiting_add_balance_amount = State()
    waiting_sub_balance_id     = State()
    waiting_sub_balance_amount = State()
    waiting_broadcast          = State()


class FavoriteStates(StatesGroup):
    waiting_add_phone       = State()
    waiting_add_label       = State()
    waiting_rename_label    = State()
    waiting_search_query    = State()
    waiting_recharge_amount = State()


class HistoryStates(StatesGroup):
    waiting_search_query   = State()
    waiting_favorite_label = State()


class DistributorAdminStates(StatesGroup):
    """Admin-only FSM for Distributor Management (Phase 1: Foundation).
    Never touches recharge/wallet/OneClick logic."""
    waiting_create_telegram_id = State()
    waiting_create_full_name   = State()
    waiting_create_phone       = State()
    waiting_search_query       = State()


class DistributorWalletStates(StatesGroup):
    """Admin-only FSM for Distributor Wallet operations (Phase 2).
    Never touches RechargeService / OneClickAPI / WalletService."""
    waiting_amount = State()   # admin enters credit or debit amount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _lang(db: Database, telegram_id: int) -> str:
    user = await db.get_user(telegram_id)
    return user["language"] if user else "ar"


async def _user(db: Database, telegram_id: int) -> Optional[dict]:
    return await db.get_user(telegram_id)


def _is_admin(telegram_id: int, config: Config) -> bool:
    return telegram_id in config.ADMIN_IDS




async def _show_activy_offers(
    message: Message, phone: str, operator: str, lang: str,
    db: Database, user_id: int, api: OneClickAPI,
) -> None:
    """Fetch live fixed plans for `operator` and show them, or report unavailability."""
    plans_result = await api.get_fixed_plans(operator)
    if not plans_result["success"] or not plans_result["plans"]:
        await message.answer(get_text("activy_no_plans", lang))
        await db.log(
            "activy_plans_unavailable",
            f"phone={phone} operator={operator} error={plans_result.get('error_message', '')}",
            user_id, level="WARNING",
        )
        return

    await message.answer(
        get_text("activy_offers_title", lang, phone=phone),
        reply_markup=activy_offers_keyboard(phone, operator, plans_result["plans"], lang),
    )
    await db.log("activy_intent", f"phone={phone} operator={operator}", user_id)


async def _show_distributor_activy_offers(
    message: Message, phone: str, operator: str, lang: str,
    db: Database, user_id: int, api: OneClickAPI,
) -> None:
    """Distributor Activy equivalent of _show_activy_offers (Phase 3C).
    Fetches live plans identically but renders distributor_activy_offers_keyboard
    so tapping a plan fires DistributorActivyCallback, not ActivyCallback.
    _show_activy_offers and the customer path are completely untouched."""
    plans_result = await api.get_fixed_plans(operator)
    if not plans_result["success"] or not plans_result["plans"]:
        await message.answer(get_text("activy_no_plans", lang))
        await db.log(
            "dist_activy_plans_unavailable",
            f"phone={phone} operator={operator} error={plans_result.get('error_message', '')}",
            user_id, level="WARNING",
        )
        return

    await message.answer(
        get_text("activy_offers_title", lang, phone=phone),
        reply_markup=distributor_activy_offers_keyboard(phone, operator, plans_result["plans"], lang),
    )
    await db.log("dist_activy_intent", f"phone={phone} operator={operator}", user_id)


async def _show_history_list(
    message: Message, db: Database, user_id: int, lang: str,
    page: int, search: Optional[str], date_filter: Optional[str], status_filter: Optional[str],
    config: Config, edit: bool = False,
) -> None:
    """Read-only: fetches and renders one page of the transaction history
    list. Never writes to the DB."""
    page_size = config.HISTORY_PAGE_SIZE
    total = await db.count_transactions_filtered(user_id, search, date_filter, status_filter)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    txns = await db.list_transactions_paginated(
        user_id, limit=page_size, offset=page * page_size,
        search=search, date_filter=date_filter, status_filter=status_filter,
    )

    if not txns:
        text = get_text("history_empty", lang)
    else:
        text = get_text("history_list_title", lang, count=str(total))

    kb = history_list_keyboard(
        txns, page, total_pages,
        has_search=bool(search), has_filter=bool(date_filter or status_filter),
        lang=lang,
    )
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


def _build_favorites_text(favorites: list, lang: str) -> str:
    entries = ""
    for fav in favorites:
        operator = OperatorDetector.detect(fav["phone"])
        entries += get_text(
            "favorites_entry_line", lang,
            label=fav["label"], phone=fav["phone"],
            operator=OperatorDetector.label(operator, lang),
        ) + "\n"
    return entries.strip()


async def _show_favorites_list(message: Message, db: Database, user_id: int, lang: str, edit: bool = False) -> None:
    favorites = await db.list_favorites(user_id, sort="recent")
    if not favorites:
        text = get_text("favorites_empty", lang)
    else:
        text = get_text("favorites_list_title", lang, entries=_build_favorites_text(favorites, lang))
    kb = favorites_list_keyboard(favorites, lang)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, config: Config, state: FSMContext) -> None:
    user = message.from_user
    if not user:
        return
    db_user = await db.get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name or user.first_name or "User",
    )
    if db_user.get("is_banned") and not _is_admin(user.id, config):
        await message.answer(get_text("banned", db_user["language"]))
        return

    # حذف رسالة /start حتى لا تتراكم رسائل الواجهة
    try:
        await message.delete()
    except Exception:
        pass

    lang = db_user["language"]
    await db.log("start", user_id=db_user["id"])
    role = await resolve_role(user.id, db, config.ADMIN_IDS)
    if role == ROLE_DISTRIBUTOR:
        kb = distributor_reply_keyboard(lang)
    else:
        kb = utility_reply_keyboard(lang, _is_admin(user.id, config))
    sent = await message.answer(
        get_text("welcome", lang, name=user.first_name or "User"),
        reply_markup=kb,
    )
    await state.update_data(home_message_id=sent.message_id)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@router.message(Command("help"))
async def cmd_help(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if not user:
        return
    db_user = await db.get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name or user.first_name or "User",
    )
    lang = db_user["language"]
    await db.log("help", user_id=db_user["id"])
    await message.answer(get_text("help_text", lang))


# ---------------------------------------------------------------------------
# /balance
# ---------------------------------------------------------------------------

@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(
        f"🆔 Telegram ID الخاص بك:\n<code>{message.from_user.id}</code>"
    )


@router.message(Command("balance"))
async def cmd_balance(
    message: Message,
    db: Database,
    config: Config,
    api: OneClickAPI,
) -> None:
    user = message.from_user
    if not user:
        return

    lang = await _lang(db, user.id)

    # تنظيف رسالة المستخدم «محفظتي» أو /balance
    try:
        await message.delete()
    except Exception:
        pass

    # محفظة المستخدم الداخلية في bot.db
    balance = await db.get_balance(user.id)

    await message.answer(
        get_text(
            "wallet_title",
            lang,
            balance=format_amount(balance),
        ),
        reply_markup=wallet_keyboard(lang),
    )

# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

@router.message(Command("history"))
async def cmd_history(message: Message, db: Database, config: Config, state: FSMContext) -> None:
    user = message.from_user
    if not user:
        return
    db_user = await _user(db, user.id)
    if not db_user:
        return
    lang = db_user["language"]
    await state.clear()
    await _show_history_list(message, db, db_user["id"], lang, 0, None, None, None, config)


# ---------------------------------------------------------------------------
# /language
# ---------------------------------------------------------------------------

@router.message(Command("language"))
async def cmd_language(message: Message, db: Database) -> None:
    user = message.from_user
    if not user:
        return
    lang = await _lang(db, user.id)
    await message.answer(get_text("choose_language", lang), reply_markup=language_keyboard())


# ---------------------------------------------------------------------------
# /admin
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if not user:
        return
    lang = await _lang(db, user.id)
    if not _is_admin(user.id, config):
        await message.answer(get_text("admin_not_authorized", lang))
        return
    await message.answer(
        get_text("admin_panel", lang),
        reply_markup=admin_keyboard(lang, config.MOCK_MODE),
    )


# ---------------------------------------------------------------------------
# /validate  (admin only)
# ---------------------------------------------------------------------------

@router.message(Command("validate"))
async def cmd_validate(message: Message, db: Database, config: Config, api: OneClickAPI) -> None:
    user = message.from_user
    if not user:
        return
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await message.answer(get_text("admin_not_authorized", lang))
        return

    status_msg = await message.answer(get_text("validate_checking", lang))

    # Call both endpoints concurrently
    import asyncio as _asyncio
    validate_result, balance_result = await _asyncio.gather(
        api.validate_api_key(),
        api.get_account_balance(),
        return_exceptions=True,
    )

    # Resolve exceptions (gather with return_exceptions=True)
    if isinstance(validate_result, Exception):
        validate_result = {"valid": False, "error_message": str(validate_result)}
    if isinstance(balance_result, Exception):
        balance_result = {"success": False, "balance": 0.0, "error_message": str(balance_result)}

    if validate_result.get("valid"):
        if balance_result.get("success"):
            balance_str = f"{balance_result['balance']:,.2f} DZD"
        else:
            balance_str = get_text("validate_balance_unavailable", lang)

        text = get_text(
            "validate_valid", lang,
            username=validate_result.get("username", ""),
            key_type=validate_result.get("key_type", ""),
            scope=validate_result.get("scope", ""),
            balance=balance_str,
            url=config.ONECLICK_API_URL,
        )
    else:
        text = get_text(
            "validate_invalid", lang,
            error=validate_result.get("error_message", "Unknown error"),
        )

    await status_msg.edit_text(text)
    await db.log("admin_validate_key", f"valid={validate_result.get('valid')} type={validate_result.get('key_type', '')}")


# ---------------------------------------------------------------------------
# Text input handler
# ---------------------------------------------------------------------------

@router.message(F.text)
async def handle_text(
    message: Message,
    db: Database,
    config: Config,
    state: FSMContext,
    api: OneClickAPI,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
) -> None:
    user = message.from_user
    if not user or not message.text:
        return

    db_user = await db.get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name or user.first_name or "User",
    )
    if db_user.get("is_banned") and not _is_admin(user.id, config):
        await message.answer(get_text("banned", db_user["language"]))
        return

    lang = db_user["language"]
    text = message.text.strip()
    current_state = await state.get_state()

    # Persistent reply-keyboard utility buttons (Balance/History/Games/
    # Gift Cards/Language/Help/Admin) — intercepted before FSM/recharge
    # parsing since they're plain text but must not be treated as a phone
    # number or fall through to "unknown command".
    if text in (get_text("btn_balance", "ar"), get_text("btn_balance", "en")):
        await cmd_balance(message, db, config, api)
        return
    if text in (get_text("btn_history", "ar"), get_text("btn_history", "en")):
        await cmd_history(message, db, config, state)
        return
    if text in (get_text("btn_games", "ar"), get_text("btn_games", "en")):
        data = await state.get_data()
        home_message_id = data.get("home_message_id")

        if home_message_id:
            try:
                await message.bot.delete_message(
                    chat_id=message.chat.id,
                    message_id=home_message_id,
                )
            except Exception:
                pass

        try:
            await message.delete()
        except Exception:
            pass

        sent = await message.answer(
            get_text("games_menu", lang),
            reply_markup=games_menu_keyboard(lang),
        )
        await state.update_data(home_message_id=sent.message_id)
        return

    if text in (get_text("btn_gift_cards", "ar"), get_text("btn_gift_cards", "en")):
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            get_text("gift_cards_menu", lang),
            reply_markup=gift_cards_menu_keyboard(lang),
        )
        return
    if text in (get_text("btn_language", "ar"), get_text("btn_language", "en")):
        await cmd_language(message, db)
        return
    if text in (get_text("btn_help", "ar"), get_text("btn_help", "en")):
        try:
            await message.delete()
        except Exception:
            pass

        await message.answer(
            get_text("help_text", lang),
            reply_markup=help_keyboard(lang),
        )
        return
    if text in (get_text("btn_admin", "ar"), get_text("btn_admin", "en")):
        await cmd_admin(message, db, config)
        return
    if text in (get_text("btn_favorites", "ar"), get_text("btn_favorites", "en")):
        await _show_favorites_list(message, db, db_user["id"], lang)
        return

    # Distributor self-service keyboard buttons (Phase 3B)
    if text in (get_text("btn_dist_wallet", "ar"), get_text("btn_dist_wallet", "en")):
        dist = await distributor_service.get_by_telegram_id(user.id)
        if dist:
            await _show_dist_self_wallet(message, dist, distributor_wallet_service, lang)
        else:
            await message.answer(get_text("dist_self_no_account", lang))
        return
    if text in (get_text("btn_dist_history", "ar"), get_text("btn_dist_history", "en")):
        dist = await distributor_service.get_by_telegram_id(user.id)
        if dist:
            await _show_dist_self_ledger(message, dist["id"], distributor_wallet_service, config, lang)
        else:
            await message.answer(get_text("dist_self_no_account", lang))
        return

    # Favorite Numbers FSM intercepts
    if current_state == FavoriteStates.waiting_add_phone:
        await _fsm_favorite_add_phone(message, text, db, state, lang)
        return
    if current_state == FavoriteStates.waiting_add_label:
        await _fsm_favorite_add_label(message, text, db, db_user["id"], state, lang, config)
        return
    if current_state == FavoriteStates.waiting_rename_label:
        await _fsm_favorite_rename_label(message, text, db, db_user["id"], state, lang)
        return
    if current_state == FavoriteStates.waiting_search_query:
        await _fsm_favorite_search_query(message, text, db, db_user["id"], state, lang)
        return
    if current_state == FavoriteStates.waiting_recharge_amount:
        await _fsm_favorite_recharge_amount(message, text, db, db_user["id"], state, lang)
        return

    # Transaction History FSM intercepts
    if current_state == HistoryStates.waiting_search_query:
        await _fsm_history_search_query(message, text, db, db_user["id"], state, lang, config)
        return
    if current_state == HistoryStates.waiting_favorite_label:
        await _fsm_history_favorite_label(message, text, db, db_user["id"], state, lang, config)
        return

    # Admin FSM intercepts
    if current_state == AdminStates.waiting_add_balance_id:
        await _fsm_add_balance_id(message, text, db, config, state, lang)
        return
    if current_state == AdminStates.waiting_add_balance_amount:
        await _fsm_add_balance_amount(message, text, db, config, state, lang)
        return
    if current_state == AdminStates.waiting_sub_balance_id:
        await _fsm_sub_balance_id(message, text, db, config, state, lang)
        return
    if current_state == AdminStates.waiting_sub_balance_amount:
        await _fsm_sub_balance_amount(message, text, db, config, state, lang)
        return
    if current_state == AdminStates.waiting_broadcast:
        await _fsm_broadcast(message, text, db, config, state, lang)
        return

    # Distributor Management admin FSM intercepts (Phase 1: Foundation)
    if current_state == DistributorAdminStates.waiting_create_telegram_id:
        await _fsm_distributor_create_telegram_id(message, text, state, lang)
        return
    if current_state == DistributorAdminStates.waiting_create_full_name:
        await _fsm_distributor_create_full_name(message, text, state, lang)
        return
    if current_state == DistributorAdminStates.waiting_create_phone:
        await _fsm_distributor_create_phone(
            message, text, db, config, distributor_service, state, lang
        )
        return
    if current_state == DistributorAdminStates.waiting_search_query:
        await _fsm_distributor_search_query(message, text, config, distributor_service, state, lang)
        return

    # Distributor Wallet admin FSM intercepts (Phase 2: Wallet & Ledger)
    if current_state == DistributorWalletStates.waiting_amount:
        await _fsm_dwlt_amount(
            message, text, distributor_service, distributor_wallet_service, config, state, lang
        )
        return

    # Standard recharge: phone*amount — detect operator and show it
    parsed = parse_recharge_input(text)
    if parsed:
        # حذف رسالة المستخدم بعد التعرف عليها كطلب فليكسي صالح
        try:
            await message.delete()
        except Exception:
            pass

        phone, amount = parsed
        operator = OperatorDetector.detect(phone)

        # Distributor routing — show the distributor confirm keyboard so the
        # distributor wallet is debited, not the customer wallet.
        # The customer flow below is completely untouched.
        role = await resolve_role(user.id, db, config.ADMIN_IDS)
        if role == ROLE_DISTRIBUTOR:
            if operator == "unknown":
                await message.answer(
                    get_text("activy_choose_operator", lang, phone=phone),
                    reply_markup=distributor_std_operator_choice_keyboard(phone, amount, lang),
                )
                await db.log("recharge_operator_unknown",
                             f"dist phone={phone} amount={amount}", db_user["id"])
                return
            op_label = OperatorDetector.label(operator, lang)
            token = secrets.token_hex(8)
            await message.answer(
                get_text("recharge_confirm", lang,
                         phone=phone, amount=str(amount), operator=op_label),
                reply_markup=distributor_confirm_recharge_keyboard(
                    phone, amount, lang, operator=operator, token=token
                ),
            )
            await db.log("dist_recharge_intent",
                         f"phone={phone} amount={amount} op={operator}", db_user["id"])
            return

        # Customer flow (unchanged) ──────────────────────────────────────────
        if operator == "unknown":
            # Don't reject the number — let the user pick the operator manually.
            await message.answer(
                get_text("activy_choose_operator", lang, phone=phone),
                reply_markup=std_operator_choice_keyboard(phone, amount, lang),
            )
            await db.log("recharge_operator_unknown", f"phone={phone} amount={amount}", db_user["id"])
            return

        op_label = OperatorDetector.label(operator, lang)
        token = secrets.token_hex(8)
        await message.answer(
            get_text("recharge_confirm", lang,
                     phone=phone, amount=str(amount), operator=op_label),
            reply_markup=confirm_recharge_keyboard(phone, amount, lang, token=token),
        )
        await db.log("recharge_intent", f"phone={phone} amount={amount} op={operator}", db_user["id"])
        return

    # Activy: phone only — detect operator, fetch live plans from OneClick
    if is_algerian_phone(text):
        operator = OperatorDetector.detect(text)

        # Distributor routing — show distributor Activy keyboards so the
        # distributor wallet is debited, not the customer wallet.
        # The customer flow below is completely untouched.
        role = await resolve_role(user.id, db, config.ADMIN_IDS)
        if role == ROLE_DISTRIBUTOR:
            if operator == "unknown":
                await message.answer(
                    get_text("activy_choose_operator", lang, phone=text),
                    reply_markup=distributor_activy_operator_choice_keyboard(text, lang),
                )
                await db.log("dist_activy_operator_unknown", f"phone={text}", db_user["id"])
                return
            await _show_distributor_activy_offers(message, text, operator, lang, db, db_user["id"], api)
            return

        # Customer flow (unchanged) ──────────────────────────────────────────
        if operator == "unknown":
            # Don't reject the number — let the user pick the operator manually.
            await message.answer(
                get_text("activy_choose_operator", lang, phone=text),
                reply_markup=activy_operator_choice_keyboard(text, lang),
            )
            await db.log("activy_operator_unknown", f"phone={text}", db_user["id"])
            return

        await _show_activy_offers(message, text, operator, lang, db, db_user["id"], api)
        return

    await message.answer(get_text("unknown_command", lang))


# ---------------------------------------------------------------------------
# Menu callbacks
# ---------------------------------------------------------------------------

@router.callback_query(MenuCallback.filter())
async def menu_callback(
    query: CallbackQuery,
    callback_data: MenuCallback,
    db: Database,
    config: Config,
    state: FSMContext,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)
    await state.clear()
    action = callback_data.action

    if action == "games":
        await query.message.edit_text(
            get_text("games_menu", lang),
            reply_markup=games_menu_keyboard(lang),
        )
    elif action == "gift_cards":
        await query.message.edit_text(
            get_text("gift_cards_menu", lang),
            reply_markup=gift_cards_menu_keyboard(lang),
        )
    elif action == "balance":
        balance = await db.get_balance(user.id)
        await query.message.edit_text(
            get_text("wallet_title", lang, balance=format_amount(balance)),
            reply_markup=wallet_keyboard(lang),
        )
    elif action == "history":
        db_user = await _user(db, user.id)
        if db_user:
            await _show_history_list(query.message, db, db_user["id"], lang, 0, None, None, None, config, edit=True)
    elif action == "language":
        await query.message.edit_text(
            get_text("choose_language", lang),
            reply_markup=language_keyboard(),
        )
    elif action == "home":
        db_user = await _user(db, user.id)
        if db_user:
            await query.message.edit_text(
                get_text("welcome", lang, name=user.first_name or "User"),
            )

    elif action == "cancel":
        await query.message.edit_text(get_text("recharge_cancelled", lang))
    elif action == "close":
        await query.message.edit_text(get_text("history_closed", lang))

    await query.answer()


# ---------------------------------------------------------------------------
# Tracking card — "Check Now" (purely presentational re-read, no OneClick
# calls, no wallet/transaction writes)
# ---------------------------------------------------------------------------

@router.callback_query(CheckNowCallback.filter())
async def check_now_callback(
    query: CallbackQuery,
    callback_data: CheckNowCallback,
    db: Database,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    tx = await db.get_transaction(callback_data.tx_id, db_user["id"])
    if tx and tx["status"] in ("pending", "processing"):
        await query.answer(get_text("tracking_still_processing", lang), show_alert=True)
    else:
        # Already resolved — the tracker should already have edited this
        # same message to its final state. Just acknowledge the tap.
        await query.answer()


# ---------------------------------------------------------------------------
# Standard recharge confirm
# ---------------------------------------------------------------------------

@router.callback_query(ConfirmCallback.filter())
async def recharge_confirm_callback(
    query: CallbackQuery,
    callback_data: ConfirmCallback,
    db: Database,
    config: Config,
    recharge_service: RechargeService,
    tracker: RechargeTracker,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    chat_id = query.message.chat.id

    logger.info(
        "Recharge completion flow: confirm tapped — chat_id=%s phone=%s amount=%s operator=%s",
        chat_id, callback_data.phone, callback_data.amount, callback_data.operator,
    )

    # Claim the idempotency token *before* touching the wallet or calling
    # OneClick, exactly like the Activy confirm flow. If this exact confirm
    # was already received (Telegram retry, user double-tap), the claim
    # fails and we return immediately without a second API call.
    if callback_data.token:
        claimed = await db.claim_idempotency_key(callback_data.token)
        if not claimed:
            await query.answer(get_text("activy_duplicate_request", lang), show_alert=True)
            return

    # Show the tracking card immediately, then hand the actual OneClick
    # call off to a background task — this is the "instant confirm" UX.
    # RechargeService.process_standard is unchanged and unmodified below.
    await query.message.edit_text(get_text("tracking_submitted", lang))
    await query.answer()

    operator = callback_data.operator
    amount = callback_data.amount
    phone = callback_data.phone
    token = callback_data.token

    async def _do_recharge() -> Dict[str, Any]:
        logger.info(
            "Recharge completion flow: calling process_standard — chat_id=%s phone=%s amount=%s",
            chat_id, phone, amount,
        )
        result = await recharge_service.process_standard(
            telegram_id=user.id,
            db_user_id=db_user["id"],
            phone=phone,
            amount=amount,
            operator=operator,
        )
        logger.info(
            "Recharge completion flow: process_standard returned — chat_id=%s phone=%s success=%s "
            "reference=%s reason=%s",
            chat_id, phone, result.get("success"),
            result.get("reference"), result.get("reason"),
        )
        if token:
            await db.finish_idempotency_key(token, "success" if result.get("success") else "failed")
        return result

    async def _render_success_async(result: Dict[str, Any]):
        op_label = OperatorDetector.label(result.get("operator") or operator or "unknown", lang)
        balance = await db.get_balance(user.id)
        text = get_text("recharge_success", lang,
                        phone=result["phone"],
                        operator=op_label,
                        amount=str(result["amount"]),
                        balance=format_amount(balance))
        return text, None

    def _render_failure(result: Dict[str, Any]):
        op_label = OperatorDetector.label(result.get("operator") or operator or "unknown", lang)
        if result.get("reason") == "insufficient_balance":
            text = get_text("insufficient_balance", lang,
                            balance=format_amount(result["balance"]),
                            required=format_amount(result["required"]))
            return text, None
        reason_key = "error_reason_provider" if result.get("reason") == "provider_error" else "error_reason_api"
        text = get_text("recharge_failed_card", lang,
                        phone=phone,
                        operator=op_label,
                        amount=str(amount),
                        reason=get_text(reason_key, lang))
        kb = recharge_failure_keyboard(phone, amount, lang, operator=operator)
        return text, kb

    async def _run_and_render() -> Dict[str, Any]:
        # Wraps process_standard so the tracker's render callbacks (which
        # must stay sync per the shared RenderFn signature) can still use
        # the async get_balance() call needed for the success card.
        result = await _do_recharge()
        if result.get("success"):
            result["_rendered"] = await _render_success_async(result)
        return result

    def _render_success_final(result: Dict[str, Any]):
        return result["_rendered"]

    tracker.track(
        chat_id=chat_id,
        message_id=query.message.message_id,
        user_id=db_user["id"],
        phone=phone,
        lang=lang,
        coro=_run_and_render(),
        render_success=_render_success_final,
        render_failure=_render_failure,
    )


# ---------------------------------------------------------------------------
# Distributor recharge confirm callback (Phase 3A)
# ---------------------------------------------------------------------------

@router.callback_query(DistributorConfirmCallback.filter())
async def distributor_recharge_confirm_callback(
    query: CallbackQuery,
    callback_data: DistributorConfirmCallback,
    db: Database,
    config: Config,
    distributor_service: DistributorService,
    distributor_recharge_service: DistributorRechargeService,
    distributor_wallet_service: DistributorWalletService,
    tracker: RechargeTracker,
) -> None:
    """Distributor-funded standard recharge confirm/cancel.
    Mirrors recharge_confirm_callback but calls DistributorRechargeService
    (which debits the distributor wallet, not the customer wallet).
    Customer recharge flow is completely untouched."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    chat_id = query.message.chat.id

    logger.info(
        "Dist recharge confirm — chat_id=%s phone=%s amount=%s operator=%s",
        chat_id, callback_data.phone, callback_data.amount, callback_data.operator,
    )

    # Claim idempotency token (same pattern as customer flow).
    if callback_data.token:
        claimed = await db.claim_idempotency_key(callback_data.token)
        if not claimed:
            await query.answer(get_text("activy_duplicate_request", lang), show_alert=True)
            return

    # Look up the distributor DB row — needed to pass distributor_id to service.
    dist = await distributor_service.get_by_telegram_id(user.id)
    if not dist:
        await query.message.edit_text(get_text("recharge_failed", lang))
        await query.answer()
        return

    # Instant tracking card — actual call runs in background via RechargeTracker.
    await query.message.edit_text(get_text("tracking_submitted", lang))
    await query.answer()

    operator = callback_data.operator
    amount   = callback_data.amount
    phone    = callback_data.phone
    token    = callback_data.token
    dist_id  = dist["id"]

    async def _do_dist_recharge() -> Dict[str, Any]:
        logger.info(
            "Dist recharge: calling process_standard — chat_id=%s dist_id=%s phone=%s amount=%s",
            chat_id, dist_id, phone, amount,
        )
        result = await distributor_recharge_service.process_standard(
            distributor_id=dist_id,
            db_user_id=db_user["id"],
            phone=phone,
            amount=amount,
            operator=operator,
        )
        logger.info(
            "Dist recharge: process_standard returned — chat_id=%s success=%s reason=%s ref=%s",
            chat_id, result.get("success"), result.get("reason"), result.get("reference"),
        )
        if token:
            await db.finish_idempotency_key(token, "success" if result.get("success") else "failed")
        return result

    async def _render_dist_success_async(result: Dict[str, Any]):
        op_label = OperatorDetector.label(result.get("operator") or operator or "unknown", lang)
        balance = await distributor_wallet_service.get_balance(dist_id) or 0.0
        dzd = "دج" if lang == "ar" else "DZD"
        text = get_text("dist_recharge_success", lang,
                        phone=result["phone"],
                        operator=op_label,
                        amount=str(result["amount"]),
                        balance=f"{int(balance):,} {dzd}")
        return text, None

    def _render_dist_failure(result: Dict[str, Any]):
        op_label = OperatorDetector.label(result.get("operator") or operator or "unknown", lang)
        reason = result.get("reason")

        if reason == "insufficient_balance":
            text = get_text("insufficient_balance", lang,
                            balance=format_amount(result.get("balance", 0)),
                            required=format_amount(result.get("required", amount)))
            return text, None

        if reason == "debit_failed":
            # Critical: OneClick succeeded but wallet debit failed — do not
            # show a retry button (would cause a double-recharge).
            text = get_text("dist_debit_failed", lang, phone=phone, amount=str(amount))
            return text, None

        # provider_error, api_error, distributor_not_found, distributor_suspended
        reason_key = "error_reason_provider" if reason == "provider_error" else "error_reason_api"
        text = get_text("recharge_failed_card", lang,
                        phone=phone,
                        operator=op_label,
                        amount=str(amount),
                        reason=get_text(reason_key, lang))
        kb = distributor_recharge_failure_keyboard(phone, amount, lang, operator=operator)
        return text, kb

    async def _run_and_render_dist() -> Dict[str, Any]:
        result = await _do_dist_recharge()
        if result.get("success"):
            result["_rendered"] = await _render_dist_success_async(result)
        return result

    def _render_dist_success_final(result: Dict[str, Any]):
        return result["_rendered"]

    tracker.track(
        chat_id=chat_id,
        message_id=query.message.message_id,
        user_id=db_user["id"],
        phone=phone,
        lang=lang,
        coro=_run_and_render_dist(),
        render_success=_render_dist_success_final,
        render_failure=_render_dist_failure,
    )


@router.callback_query(DistributorStdOperatorCallback.filter())
async def distributor_std_operator_callback(
    query: CallbackQuery,
    callback_data: DistributorStdOperatorCallback,
    db: Database,
) -> None:
    """Distributor manually picked an operator after auto-detection failed.
    Continues into the distributor confirm screen — customer StdOperatorCallback
    handler and its confirm keyboard are not involved."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    op_label = OperatorDetector.label(callback_data.operator, lang)
    token = secrets.token_hex(8)
    await query.message.edit_text(
        get_text("recharge_confirm", lang,
                 phone=callback_data.phone, amount=str(callback_data.amount), operator=op_label),
        reply_markup=distributor_confirm_recharge_keyboard(
            callback_data.phone, callback_data.amount, lang,
            operator=callback_data.operator, token=token,
        ),
    )
    await db.log(
        "dist_recharge_intent",
        f"phone={callback_data.phone} amount={callback_data.amount} "
        f"op={callback_data.operator} manual=1",
        db_user["id"],
    )
    await query.answer()


# ---------------------------------------------------------------------------
# Phase 3C — Distributor Activy callbacks
# Mirrors the customer Activy section exactly.  All three handlers are
# additive — no existing handlers are modified.
# ---------------------------------------------------------------------------

@router.callback_query(DistributorActivyOperatorCallback.filter())
async def distributor_activy_operator_callback(
    query: CallbackQuery,
    callback_data: DistributorActivyOperatorCallback,
    db: Database,
    api: OneClickAPI,
) -> None:
    """Distributor manually picked an operator after Activy auto-detection
    failed.  Shows the distributor Activy offers grid — customer
    ActivyOperatorCallback handler and its keyboards are not involved."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    plans_result = await api.get_fixed_plans(callback_data.operator)
    if not plans_result["success"] or not plans_result["plans"]:
        await query.message.edit_text(get_text("activy_no_plans", lang))
        await db.log(
            "dist_activy_plans_unavailable",
            f"phone={callback_data.phone} operator={callback_data.operator} "
            f"error={plans_result.get('error_message', '')}",
            db_user["id"], level="WARNING",
        )
        await query.answer()
        return

    await query.message.edit_text(
        get_text("activy_offers_title", lang, phone=callback_data.phone),
        reply_markup=distributor_activy_offers_keyboard(
            callback_data.phone, callback_data.operator, plans_result["plans"], lang
        ),
    )
    await db.log(
        "dist_activy_intent",
        f"phone={callback_data.phone} operator={callback_data.operator} manual=1",
        db_user["id"],
    )
    await query.answer()


@router.callback_query(DistributorActivyNavCallback.filter())
async def distributor_activy_nav_callback(
    query: CallbackQuery,
    callback_data: DistributorActivyNavCallback,
    db: Database,
) -> None:
    """Back/Cancel from the distributor Activy offers grid — purely
    presentational navigation, no OneClick calls, no wallet/db writes.
    Customer ActivyNavCallback handler is not involved."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    if callback_data.action == "cancel":
        await query.message.edit_text(get_text("recharge_cancelled", lang))
        await query.answer()
        return

    # "back" — return to the distributor operator-choice screen
    await query.message.edit_text(
        get_text("activy_choose_operator", lang, phone=callback_data.phone),
        reply_markup=distributor_activy_operator_choice_keyboard(callback_data.phone, lang),
    )
    await query.answer()


@router.callback_query(DistributorActivyCallback.filter())
async def distributor_activy_confirm_callback(
    query: CallbackQuery,
    callback_data: DistributorActivyCallback,
    db: Database,
    config: Config,
    api: OneClickAPI,
    distributor_service: DistributorService,
    distributor_recharge_service: DistributorRechargeService,
    distributor_wallet_service: DistributorWalletService,
    tracker: RechargeTracker,
) -> None:
    """Distributor Activy offer selection/confirm/cancel.
    Mirrors activy_callback in structure but calls DistributorRechargeService
    (which debits the distributor wallet, not the customer wallet).
    The customer ActivyCallback handler is completely untouched."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    operator = callback_data.operator

    # Re-fetch live plan data on every tap — same pattern as customer flow.
    plans_result = await api.get_fixed_plans(operator) if operator != "unknown" else {"success": False, "plans": []}
    plan = next(
        (p for p in plans_result.get("plans", []) if p.get("code") == callback_data.plan_code),
        None,
    )
    if not plan:
        await query.answer(get_text("invalid_offer", lang), show_alert=True)
        return

    plan_name   = plan.get("name", plan["code"])
    plan_amount = float(plan.get("amount", 0))

    # ── confirmed == 0: show the confirm screen ────────────────────────────
    if callback_data.confirmed == 0:
        text = get_text("activy_confirm", lang,
                        phone=callback_data.phone,
                        operator=OperatorDetector.label(operator, lang),
                        offer=plan_name,
                        price=format_amount(plan_amount))
        token = secrets.token_hex(8)
        await query.message.edit_text(
            text,
            reply_markup=distributor_activy_confirm_keyboard(
                callback_data.phone, callback_data.operator, callback_data.plan_code, token, lang
            ),
        )
        await query.answer()
        return

    # ── confirmed == -1: cancel ────────────────────────────────────────────
    if callback_data.confirmed == -1:
        await query.message.edit_text(
            get_text("games_menu", lang),
            reply_markup=games_menu_keyboard(lang),
        )
        await query.answer()
        return

    # ── confirmed == 1: claim idempotency token before any work ───────────
    if callback_data.token:
        claimed = await db.claim_idempotency_key(callback_data.token)
        if not claimed:
            await query.answer(get_text("activy_duplicate_request", lang), show_alert=True)
            return

    # Look up distributor DB row — needed to pass distributor_id to service.
    dist = await distributor_service.get_by_telegram_id(user.id)
    if not dist:
        await query.message.edit_text(get_text("recharge_failed", lang))
        await query.answer()
        return

    # Instant tracking card — actual call runs in background via RechargeTracker.
    await query.message.edit_text(get_text("tracking_submitted", lang))
    await query.answer()

    chat_id  = query.message.chat.id
    phone    = callback_data.phone
    token    = callback_data.token
    dist_id  = dist["id"]

    async def _do_dist_activy() -> Dict[str, Any]:
        logger.info(
            "Dist activy: calling process_activy — chat_id=%s dist_id=%s phone=%s offer=%s",
            chat_id, dist_id, phone, callback_data.plan_code,
        )
        result = await distributor_recharge_service.process_activy(
            distributor_id=dist_id,
            db_user_id=db_user["id"],
            phone=phone,
            plan_code=callback_data.plan_code,
            plan_name=plan_name,
            amount=plan_amount,
        )
        logger.info(
            "Dist activy: process_activy returned — chat_id=%s success=%s reason=%s ref=%s",
            chat_id, result.get("success"), result.get("reason"), result.get("reference"),
        )
        if token:
            await db.finish_idempotency_key(token, "success" if result.get("success") else "failed")
        if result.get("success"):
            od       = result["offer"]
            name     = od["name_ar"] if lang == "ar" else od["name_en"]
            balance  = await distributor_wallet_service.get_balance(dist_id) or 0.0
            dzd      = "دج" if lang == "ar" else "DZD"
            op_label = OperatorDetector.label(operator, lang)
            text = get_text("dist_activy_success", lang,
                            phone=result["phone"],
                            operator=op_label,
                            offer=name,
                            balance=f"{int(balance):,} {dzd}")
            result["_rendered"] = (text, None)
        return result

    def _render_dist_activy_success(result: Dict[str, Any]):
        return result["_rendered"]

    def _render_dist_activy_failure(result: Dict[str, Any]):
        op_label = OperatorDetector.label(operator, lang)
        reason   = result.get("reason")

        if reason == "insufficient_balance":
            text = get_text("insufficient_balance", lang,
                            balance=format_amount(result.get("balance", 0)),
                            required=format_amount(result.get("required", plan_amount)))
            return text, None

        if reason == "debit_failed":
            # Critical: OneClick succeeded but wallet debit failed — no retry
            # button (would cause a double-activation on the same plan).
            text = get_text("dist_activy_debit_failed", lang, phone=phone, offer=plan_name)
            return text, None

        # provider_error, api_error, distributor_not_found, distributor_suspended
        reason_key = "error_reason_provider" if reason == "provider_error" else "error_reason_api"
        text = get_text("activy_failed_card", lang,
                        phone=phone,
                        operator=op_label,
                        offer=plan_name,
                        reason=get_text(reason_key, lang))
        kb = distributor_activy_failure_keyboard(phone, operator, callback_data.plan_code, lang)
        return text, kb

    tracker.track(
        chat_id=chat_id,
        message_id=query.message.message_id,
        user_id=db_user["id"],
        phone=phone,
        lang=lang,
        coro=_do_dist_activy(),
        render_success=_render_dist_activy_success,
        render_failure=_render_dist_activy_failure,
    )


# ---------------------------------------------------------------------------
# Activy callbacks
# ---------------------------------------------------------------------------

@router.callback_query(ActivyCallback.filter())
async def activy_callback(
    query: CallbackQuery,
    callback_data: ActivyCallback,
    db: Database,
    config: Config,
    api: OneClickAPI,
    recharge_service: RechargeService,
    tracker: RechargeTracker,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    operator = callback_data.operator
    plans_result = await api.get_fixed_plans(operator) if operator != "unknown" else {"success": False, "plans": []}
    plan = next(
        (p for p in plans_result.get("plans", []) if p.get("code") == callback_data.plan_code),
        None,
    )
    if not plan:
        await query.answer(get_text("invalid_offer", lang), show_alert=True)
        return

    plan_name = plan.get("name", plan["code"])
    plan_amount = float(plan.get("amount", 0))

    if callback_data.confirmed == 0:
        text = get_text("activy_confirm", lang,
                        phone=callback_data.phone,
                        operator=OperatorDetector.label(operator, lang),
                        offer=plan_name,
                        price=format_amount(plan_amount))
        # Mint a fresh single-use idempotency token for this confirmation
        # screen. It is echoed back on the Confirm/Cancel taps so a duplicate
        # confirm (Telegram retry or double-tap) can be detected and ignored.
        token = secrets.token_hex(8)
        await query.message.edit_text(
            text,
            reply_markup=activy_confirm_keyboard(
                callback_data.phone, callback_data.operator, callback_data.plan_code, token, lang
            ),
        )
        await query.answer()
        return

    if callback_data.confirmed == -1:
        await query.message.edit_text(
            get_text("gift_cards_menu", lang),
            reply_markup=gift_cards_menu_keyboard(lang),
        )
        await query.answer()
        return

    # confirmed == 1: claim the idempotency token *before* touching the
    # wallet or calling OneClick. If this exact confirm was already
    # received (Telegram retry, user double-tap), the claim fails and we
    # return immediately without a second API call or balance deduction.
    if callback_data.token:
        claimed = await db.claim_idempotency_key(callback_data.token)
        if not claimed:
            await query.answer(get_text("activy_duplicate_request", lang), show_alert=True)
            return

    # Show the tracking card immediately, then hand the actual OneClick
    # call off to a background task — this is the "instant confirm" UX.
    # RechargeService.process_activy is unchanged and unmodified below.
    await query.message.edit_text(get_text("tracking_submitted", lang))
    await query.answer()

    chat_id = query.message.chat.id
    phone = callback_data.phone
    token = callback_data.token

    async def _do_activy() -> Dict[str, Any]:
        result = await recharge_service.process_activy(
            telegram_id=user.id,
            db_user_id=db_user["id"],
            phone=phone,
            plan_code=callback_data.plan_code,
            plan_name=plan_name,
            amount=plan_amount,
        )
        if token:
            await db.finish_idempotency_key(token, "success" if result.get("success") else "failed")
        if result.get("success"):
            od   = result["offer"]
            name = od["name_ar"] if lang == "ar" else od["name_en"]
            balance = await db.get_balance(user.id)
            op_label = OperatorDetector.label(operator, lang)
            text = get_text("activy_success", lang,
                            phone=result["phone"],
                            operator=op_label,
                            offer=name,
                            balance=format_amount(balance))
            result["_rendered"] = (text, None)
        return result

    def _render_success(result: Dict[str, Any]):
        return result["_rendered"]

    def _render_failure(result: Dict[str, Any]):
        op_label = OperatorDetector.label(operator, lang)
        if result.get("reason") == "insufficient_balance":
            text = get_text("insufficient_balance", lang,
                            balance=format_amount(result["balance"]),
                            required=format_amount(result["required"]))
            return text, None
        reason_key = "error_reason_provider" if result.get("reason") == "provider_error" else "error_reason_api"
        text = get_text("activy_failed_card", lang,
                        phone=phone,
                        operator=op_label,
                        offer=plan_name,
                        reason=get_text(reason_key, lang))
        kb = activy_failure_keyboard(phone, operator, callback_data.plan_code, lang)
        return text, kb

    tracker.track(
        chat_id=chat_id,
        message_id=query.message.message_id,
        user_id=db_user["id"],
        phone=phone,
        lang=lang,
        coro=_do_activy(),
        render_success=_render_success,
        render_failure=_render_failure,
    )


@router.callback_query(ActivyOperatorCallback.filter())
async def activy_operator_callback(
    query: CallbackQuery,
    callback_data: ActivyOperatorCallback,
    db: Database,
    api: OneClickAPI,
) -> None:
    """User manually picked an operator after auto-detection failed."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    plans_result = await api.get_fixed_plans(callback_data.operator)
    if not plans_result["success"] or not plans_result["plans"]:
        await query.message.edit_text(get_text("activy_no_plans", lang))
        await db.log(
            "activy_plans_unavailable",
            f"phone={callback_data.phone} operator={callback_data.operator} "
            f"error={plans_result.get('error_message', '')}",
            db_user["id"], level="WARNING",
        )
        await query.answer()
        return

    await query.message.edit_text(
        get_text("activy_offers_title", lang, phone=callback_data.phone),
        reply_markup=activy_offers_keyboard(
            callback_data.phone, callback_data.operator, plans_result["plans"], lang
        ),
    )
    await db.log(
        "activy_intent",
        f"phone={callback_data.phone} operator={callback_data.operator} manual=1",
        db_user["id"],
    )
    await query.answer()


@router.callback_query(ActivyNavCallback.filter())
async def activy_nav_callback(
    query: CallbackQuery,
    callback_data: ActivyNavCallback,
    db: Database,
) -> None:
    """Back/Cancel from the Activy offers grid — purely presentational
    navigation, no recharge logic, no OneClick calls, no wallet/db writes."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    if callback_data.action == "cancel":
        await query.message.edit_text(get_text("recharge_cancelled", lang))
        await query.answer()
        return

    # "back" — return to the operator-choice screen for this phone number
    await query.message.edit_text(
        get_text("activy_choose_operator", lang, phone=callback_data.phone),
        reply_markup=activy_operator_choice_keyboard(callback_data.phone, lang),
    )
    await query.answer()


@router.callback_query(StdOperatorCallback.filter())
async def std_operator_callback(
    query: CallbackQuery,
    callback_data: StdOperatorCallback,
    db: Database,
) -> None:
    """User manually picked an operator for a standard (phone*amount) recharge
    after auto-detection failed. Continue into the normal confirm screen,
    carrying the chosen operator so process_standard() doesn't re-detect it."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    op_label = OperatorDetector.label(callback_data.operator, lang)
    token = secrets.token_hex(8)
    await query.message.edit_text(
        get_text("recharge_confirm", lang,
                 phone=callback_data.phone, amount=str(callback_data.amount), operator=op_label),
        reply_markup=confirm_recharge_keyboard(
            callback_data.phone, callback_data.amount, lang, operator=callback_data.operator, token=token
        ),
    )
    await db.log(
        "recharge_intent",
        f"phone={callback_data.phone} amount={callback_data.amount} op={callback_data.operator} manual=1",
        db_user["id"],
    )
    await query.answer()


# ---------------------------------------------------------------------------
# Games callbacks
# ---------------------------------------------------------------------------

@router.callback_query(GameSelectCallback.filter())
async def game_select_callback(
    query: CallbackQuery,
    callback_data: GameSelectCallback,
    db: Database,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)
    game = GAMES.get(callback_data.game_id)
    if not game:
        await query.answer()
        return
    name = game["name_ar"] if lang == "ar" else game["name_en"]
    await query.message.edit_text(
        get_text("game_amounts", lang, game=f"{game['emoji']} {name}"),
        reply_markup=game_packages_keyboard(callback_data.game_id, lang),
    )
    await query.answer()


@router.callback_query(GameConfirmCallback.filter())
async def game_confirm_callback(
    query: CallbackQuery,
    callback_data: GameConfirmCallback,
    db: Database,
    config: Config,
    games_service: GamesService,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    game = GAMES.get(callback_data.game_id)
    if not game:
        await query.answer()
        return

    packages = game["packages"]
    if not (0 <= callback_data.pkg_index < len(packages)):
        await query.answer()
        return
    pkg = packages[callback_data.pkg_index]
    game_name = game["name_ar"] if lang == "ar" else game["name_en"]

    if callback_data.confirmed == 0:
        await query.message.edit_text(
            get_text("game_confirm", lang,
                     game=f"{game['emoji']} {game_name}",
                     amount=str(pkg["amount"]),
                     currency=game["currency"],
                     price=str(pkg["price"])),
            reply_markup=game_confirm_keyboard(callback_data.game_id, callback_data.pkg_index, lang),
        )
        await query.answer()
        return

    if callback_data.confirmed == -1:
        await query.message.edit_text(get_text("recharge_cancelled", lang))
        await query.answer()
        return

    await query.message.edit_text(get_text("processing", lang))

    result = await games_service.process(
        telegram_id=user.id,
        db_user_id=db_user["id"],
        game_id=callback_data.game_id,
        pkg_index=callback_data.pkg_index,
    )

    if result["success"]:
        g     = result["game"]
        gname = g["name_ar"] if lang == "ar" else g["name_en"]
        text  = get_text("game_success", lang,
                         amount=str(result["amount"]),
                         currency=g["currency"],
                         game=gname,
                         ref=result.get("reference", "-"))
    elif result.get("reason") == "insufficient_balance":
        text = get_text("insufficient_balance", lang,
                        balance=format_amount(result["balance"]),
                        required=format_amount(result["required"]))
    else:
        text = get_text("recharge_failed", lang)

    await query.message.edit_text(text)
    await query.answer()


# ---------------------------------------------------------------------------
# Gift card callbacks
# ---------------------------------------------------------------------------

@router.callback_query(GiftSelectCallback.filter())
async def gift_select_callback(
    query: CallbackQuery,
    callback_data: GiftSelectCallback,
    db: Database,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)
    card = GIFT_CARDS.get(callback_data.card_type)
    if not card:
        await query.answer()
        return
    name = card["name_ar"] if lang == "ar" else card["name_en"]
    await query.message.edit_text(
        get_text("gift_amounts", lang, card=f"{card['emoji']} {name}"),
        reply_markup=gift_amounts_keyboard(callback_data.card_type, lang),
    )
    await query.answer()


@router.callback_query(GiftConfirmCallback.filter())
async def gift_confirm_callback(
    query: CallbackQuery,
    callback_data: GiftConfirmCallback,
    db: Database,
    config: Config,
    gift_card_service: GiftCardService,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    card = GIFT_CARDS.get(callback_data.card_type)
    if not card:
        await query.answer()
        return
    card_name = card["name_ar"] if lang == "ar" else card["name_en"]

    if callback_data.confirmed == 0:
        await query.message.edit_text(
            get_text("gift_confirm", lang,
                     card=f"{card['emoji']} {card_name}",
                     amount=str(callback_data.amount)),
            reply_markup=gift_confirm_keyboard(callback_data.card_type, callback_data.amount, lang),
        )
        await query.answer()
        return

    if callback_data.confirmed == -1:
        await query.message.edit_text(get_text("recharge_cancelled", lang))
        await query.answer()
        return

    await query.message.edit_text(get_text("processing", lang))

    result = await gift_card_service.process(
        telegram_id=user.id,
        db_user_id=db_user["id"],
        card_type=callback_data.card_type,
        amount=callback_data.amount,
    )

    if result["success"]:
        c     = result["card"]
        cname = c["name_ar"] if lang == "ar" else c["name_en"]
        text  = get_text("gift_success", lang,
                         card=cname,
                         amount=str(result["amount"]),
                         code=result["code"])
    elif result.get("reason") == "insufficient_balance":
        text = get_text("insufficient_balance", lang,
                        balance=format_amount(result["balance"]),
                        required=format_amount(result["required"]))
    else:
        text = get_text("recharge_failed", lang)

    await query.message.edit_text(text)
    await query.answer()


# ---------------------------------------------------------------------------
# Language callback
# ---------------------------------------------------------------------------

@router.callback_query(LangCallback.filter())
async def language_callback(
    query: CallbackQuery,
    callback_data: LangCallback,
    db: Database,
    config: Config,
) -> None:
    user = query.from_user
    lang = callback_data.code
    await db.set_language(user.id, lang)
    await db.log("language_change", f"lang={lang}")
    await query.message.edit_text(get_text("language_changed", lang))
    # edit_text can't change the persistent ReplyKeyboardMarkup (it's a
    # separate chat-level keyboard, not tied to this message) — resend the
    # bottom keyboard silently here so its labels switch language immediately.
    role = await resolve_role(user.id, db, config.ADMIN_IDS)
    if role == ROLE_DISTRIBUTOR:
        kb = distributor_reply_keyboard(lang)
    else:
        kb = utility_reply_keyboard(lang, _is_admin(user.id, config))
    await query.message.answer(
        "\u2063",  # invisible separator char — this message exists only to attach the keyboard
        reply_markup=kb,
    )
    await query.answer()


# ---------------------------------------------------------------------------
# Wallet / deposit callbacks
# ---------------------------------------------------------------------------

@router.callback_query(DepositCallback.filter())
async def deposit_callback(
    query: CallbackQuery,
    callback_data: DepositCallback,
    db: Database,
    config: Config,
    bot: Bot,
    wallet_service: WalletService,
    state: FSMContext,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    action = callback_data.action

    if action == "exit":
        await state.clear()

        try:
            await query.message.delete()
        except Exception:
            pass

        await query.message.answer(
            get_text(
                "welcome",
                lang,
                name=user.first_name or "User",
            ),
            reply_markup=utility_reply_keyboard(
                lang,
                _is_admin(user.id, config),
            ),
        )

        await query.answer()
        return

    if action == "menu":
        await query.message.edit_text(
            get_text("deposit_select_amount", lang),
            reply_markup=deposit_amounts_keyboard(lang),
        )

    elif action == "select":
        amount = callback_data.amount
        await query.message.edit_text(
            get_text("deposit_confirm_msg", lang, amount=format_amount(amount)),
            reply_markup=deposit_confirm_keyboard(amount, lang),
        )

    elif action == "confirm":
        amount = callback_data.amount
        result = await wallet_service.request_deposit(
            telegram_id=user.id,
            db_user_id=db_user["id"],
            amount=float(amount),
        )
        await query.message.edit_text(
            get_text("deposit_requested", lang, amount=format_amount(amount))
        )
        # Notify all admins
        notify_text = get_text(
            "admin_deposit_notify_title", "ar",  # admin sees Arabic by default
            name=db_user["full_name"] or user.first_name or "User",
            uid=str(user.id),
            amount=format_amount(amount),
        )
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    notify_text,
                    reply_markup=admin_deposit_action_keyboard(result["request_id"], "ar"),
                )
            except Exception as exc:
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    elif action == "cancel":
        balance = await db.get_balance(user.id)
        await query.message.edit_text(
            get_text("wallet_title", lang, balance=format_amount(balance)),
            reply_markup=wallet_keyboard(lang),
        )

    await query.answer()


# ---------------------------------------------------------------------------
# Admin deposit callbacks
# ---------------------------------------------------------------------------

@router.callback_query(AdminDepositCallback.filter())
async def admin_deposit_callback(
    query: CallbackQuery,
    callback_data: AdminDepositCallback,
    db: Database,
    config: Config,
    bot: Bot,
    wallet_service: WalletService,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    action = callback_data.action

    if action == "list":
        pending = await wallet_service.get_pending()
        if not pending:
            text = get_text("admin_deposits_empty", "ar")
        else:
            lines = [get_text("admin_deposits_title", "ar")]
            for req in pending:
                lines.append(get_text(
                    "admin_deposit_row", "ar",
                    id=str(req["id"]),
                    name=req.get("full_name") or str(req["telegram_id"]),
                    amount=f"{req['amount']:.0f}",
                    date=format_datetime(req["created_at"]),
                ))
            text = "\n".join(lines)
        await query.message.edit_text(
            text,
            reply_markup=admin_keyboard(lang, config.MOCK_MODE),
        )

    elif action == "approve":
        result = await wallet_service.approve_deposit(
            request_id=callback_data.request_id,
            admin_telegram_id=user.id,
        )
        if not result["success"]:
            reason = result.get("reason", "")
            if reason == "already_resolved":
                await query.answer(get_text("admin_deposit_already_resolved", "ar"), show_alert=True)
            else:
                await query.answer(get_text("admin_deposit_not_found", "ar"), show_alert=True)
            return

        # Edit the admin notification message
        await query.message.edit_text(
            get_text("admin_deposit_approved", "ar",
                     id=str(callback_data.request_id),
                     name=result["user_name"],
                     amount=f"{result['amount']:.0f}"),
        )
        # Notify the user
        user_lang = await _lang(db, result["user_telegram_id"])
        try:
            await bot.send_message(
                result["user_telegram_id"],
                get_text("deposit_approved_user", user_lang,
                         amount=format_amount(result["amount"]),
                         balance=format_amount(result["new_balance"])),
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s of deposit approval: %s",
                           result["user_telegram_id"], exc)

    elif action == "reject":
        result = await wallet_service.reject_deposit(
            request_id=callback_data.request_id,
            admin_telegram_id=user.id,
        )
        if not result["success"]:
            reason = result.get("reason", "")
            if reason == "already_resolved":
                await query.answer(get_text("admin_deposit_already_resolved", "ar"), show_alert=True)
            else:
                await query.answer(get_text("admin_deposit_not_found", "ar"), show_alert=True)
            return

        await query.message.edit_text(
            get_text("admin_deposit_rejected", "ar",
                     id=str(callback_data.request_id),
                     name=result["user_name"],
                     amount=f"{result['amount']:.0f}"),
        )
        user_lang = await _lang(db, result["user_telegram_id"])
        try:
            await bot.send_message(
                result["user_telegram_id"],
                get_text("deposit_rejected_user", user_lang,
                         amount=format_amount(result["amount"])),
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s of deposit rejection: %s",
                           result["user_telegram_id"], exc)

    await query.answer()


# ---------------------------------------------------------------------------
# Admin callbacks
# ---------------------------------------------------------------------------

@router.callback_query(AdminCallback.filter())
async def admin_callback(
    query: CallbackQuery,
    callback_data: AdminCallback,
    db: Database,
    config: Config,
    bot: Bot,
    state: FSMContext,
    wallet_service: WalletService,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    action = callback_data.action

    if action == "panel":
        await query.message.edit_text(
            get_text("admin_panel", lang),
            reply_markup=admin_keyboard(lang, config.MOCK_MODE),
        )

    elif action == "exit":
        role = await resolve_role(user.id, db, config.ADMIN_IDS)

        await query.message.delete()

        if role == ROLE_DISTRIBUTOR:
            kb = distributor_reply_keyboard(lang)
        else:
            kb = utility_reply_keyboard(lang, True)

        await query.message.answer(
            get_text("welcome", lang, name=user.first_name or "User"),
            reply_markup=kb,
        )

    elif action == "stats":
        user_count  = await db.count_users()
        tx_counts   = await db.count_transactions()
        pending_dep = await wallet_service.get_pending()
        mock_label  = get_text("mock_on", lang) if config.MOCK_MODE else get_text("mock_off", lang)
        await query.message.edit_text(
            get_text("admin_stats", lang,
                     users=str(user_count),
                     transactions=str(tx_counts.get("total", 0)),
                     success=str(tx_counts.get("success", 0)),
                     failed=str(tx_counts.get("failed", 0)),
                     deposits=str(len(pending_dep)),
                     mock=mock_label),
            reply_markup=admin_keyboard(lang, config.MOCK_MODE),
        )

    elif action == "users":
        users = await db.get_all_users()

        if not users:
            await query.message.edit_text(
                get_text("admin_users_empty", lang),
                reply_markup=admin_keyboard(lang, config.MOCK_MODE),
            )
        else:
            text = get_text("admin_users_title", lang)
            b = InlineKeyboardBuilder()
            unknown = get_text("admin_users_unknown", lang)

            for u in users[:20]:
                name = u["full_name"] or unknown
                banned = " 🚫" if u["is_banned"] else ""
                label = f"{name}{banned} · {u['telegram_id']}"

                b.row(
                    InlineKeyboardButton(
                        text=label,
                        callback_data=AdminCallback(
                            action="user_detail",
                            target_id=u["telegram_id"],
                        ).pack(),
                    )
                )

            b.row(
                InlineKeyboardButton(
                    text=get_text("btn_back", lang),
                    callback_data=AdminCallback(action="panel").pack(),
                )
            )

            await query.message.edit_text(
                text,
                reply_markup=b.as_markup(),
            )

    elif action == "user_detail":
        target_id = callback_data.target_id
        target_user = await db.get_user(target_id)

        if not target_user:
            await query.answer(
                get_text("admin_user_not_found", lang),
                show_alert=True,
            )
            return

        name = target_user["full_name"] or str(target_id)
        balance = format_amount(target_user["balance"])
        banned = target_user["is_banned"]

        text = (
            f"<b>{name}</b>\n"
            f"🆔 <code>{target_id}</code>\n"
            f"💰 {balance}\n"
            f"🚫 {'نعم' if banned else 'لا'}"
        )

        await query.message.edit_text(
            text,
            reply_markup=admin_user_actions_keyboard(target_id, lang, bool(target_user["is_banned"])),
        )

    elif action == "logs":
        logs = await db.get_recent_logs(30)
        if not logs:
            text = get_text("admin_logs_empty", lang)
        else:
            lines = [get_text("admin_logs_title", lang)]
            for e in logs:
                lines.append(
                    f"• [{format_datetime(e['created_at'])}] "
                    f"[{e.get('level','INFO')}] "
                    f"<code>{e['action']}</code> — {e['details'] or ''}"
                )
            text = "\n".join(lines)
        await query.message.edit_text(text, reply_markup=admin_keyboard(lang, config.MOCK_MODE))

    elif action == "txns":
        txns = await db.get_all_transactions(30)
        if not txns:
            text = get_text("admin_txns_empty", lang)
        else:
            lines = [get_text("admin_txns_title", lang)]
            for tx in txns:
                op  = f" [{tx['operator']}]" if tx.get("operator") else ""
                ref = f" #{tx['reference']}" if tx.get("reference") else ""
                lines.append(
                    f"• {tx_type_label(tx['type'], lang)}{op} | "
                    f"{tx['description'] or '-'} | "
                    f"{tx['amount']:.0f} دج | "
                    f"{tx_status_label(tx['status'], lang)}{ref} | "
                    f"{format_datetime(tx['created_at'])}"
                )
            text = "\n".join(lines)
        await query.message.edit_text(text, reply_markup=admin_keyboard(lang, config.MOCK_MODE))

    elif action == "add_balance":
        await query.message.edit_text(get_text("admin_add_balance_ask_id", lang))
        await state.set_state(AdminStates.waiting_add_balance_id)

    elif action == "add_bal":
        target_id   = callback_data.target_id
        target_user = await db.get_user(target_id)
        name = target_user["full_name"] if target_user else str(target_id)
        bal  = f"{target_user['balance']:.0f}" if target_user else "0"
        await state.update_data(target_telegram_id=target_id)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(
                text=get_text("btn_back", lang),
                callback_data=AdminCallback(
                    action="user_detail",
                    target_id=target_id,
                ).pack(),
            )
        )

        await query.message.edit_text(
            get_text("admin_add_balance_ask_amount", lang, name=name, balance=bal),
            reply_markup=b.as_markup(),
        )
        await state.set_state(AdminStates.waiting_add_balance_amount)

    elif action == "sub_bal":
        target_id   = callback_data.target_id
        target_user = await db.get_user(target_id)
        name = target_user["full_name"] if target_user else str(target_id)
        bal  = f"{target_user['balance']:.0f}" if target_user else "0"
        await state.update_data(target_telegram_id=target_id)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(
                text=get_text("btn_back", lang),
                callback_data=AdminCallback(
                    action="user_detail",
                    target_id=target_id,
                ).pack(),
            )
        )

        await query.message.edit_text(
            get_text("admin_sub_balance_ask_amount", lang, name=name, balance=bal),
            reply_markup=b.as_markup(),
        )
        await state.set_state(AdminStates.waiting_sub_balance_amount)

    elif action == "ban":
        target_id   = callback_data.target_id
        target_user = await db.get_user(target_id)
        if target_user:
            if _is_admin(target_id, config):
                await query.answer(
                    "⚠️ لا يمكن حظر حساب أدمن.",
                    show_alert=True,
                )
                return

            is_banned = bool(target_user["is_banned"])
            await db.ban_user(target_id, not is_banned)
            key = "admin_ban_done" if not is_banned else "admin_unban_done"
            await query.message.edit_text(
                get_text(key, lang, uid=str(target_id)),
                reply_markup=admin_keyboard(lang, config.MOCK_MODE),
            )
            await db.log("admin_ban", f"target={target_id} banned={not is_banned}")
        else:
            await query.answer(get_text("admin_user_not_found", lang), show_alert=True)

    elif action == "broadcast":
        await query.message.edit_text(get_text("admin_broadcast_ask", lang))
        await state.set_state(AdminStates.waiting_broadcast)

    elif action == "preview_dist":
        active = await distributor_service.list(limit=50, offset=0)
        if not active:
            await query.answer(get_text("dprev_no_active", lang), show_alert=True)
            return
        if len(active) == 1:
            dist = active[0]
            balance = await distributor_wallet_service.get_balance(dist["id"]) or 0.0
            await query.message.edit_text(
                get_text("dist_self_wallet_title", lang, balance=format_amount(balance)),
                reply_markup=dist_preview_wallet_keyboard(dist["id"], lang),
            )
        else:
            await query.message.edit_text(
                get_text("dprev_pick_title", lang),
                reply_markup=dist_preview_pick_keyboard(active, lang),
            )

    elif action == "toggle_mock":
        config.MOCK_MODE = not config.MOCK_MODE
        mock_label = get_text("mock_on", lang) if config.MOCK_MODE else get_text("mock_off", lang)
        await query.message.edit_text(
            get_text("admin_mock_toggled", lang, status=mock_label),
            reply_markup=admin_keyboard(lang, config.MOCK_MODE),
        )
        await db.log("admin_toggle_mock", f"mock_mode={config.MOCK_MODE}")

    await query.answer()


# ---------------------------------------------------------------------------
# Admin Dashboard (strictly read-only — see dashboard.py)
# ---------------------------------------------------------------------------

def _health_emoji(score: int) -> str:
    if score >= 95:
        return "🟢"
    if score >= 80:
        return "🟡"
    return "🔴"


_OPERATOR_LABELS = {
    "mobilis": "🟢 Mobilis",
    "djezzy": "🔴 Djezzy",
    "ooredoo": "🟠 Ooredoo",
}


def _format_operator(operator: Optional[str]) -> str:
    if not operator:
        return "—"
    return _OPERATOR_LABELS.get(str(operator).lower(), str(operator).capitalize())


def _render_home_text(lang: str, summary: dict) -> str:
    health = summary["health"]
    score = health["score"]
    reasons = health["reasons"]
    reasons_text = (
        "\n".join(get_text("dash_health_reason_line", lang, reason=r) for r in reasons)
        if reasons else get_text("dash_health_all_good", lang)
    )

    alerts = summary["alerts"]
    if alerts:
        lines = "\n".join(get_text("dash_action_required_line", lang, message=a["message"]) for a in alerts)
        action_required = get_text("dash_action_required_title", lang) + lines + "\n"
    else:
        action_required = ""

    last = summary["last_activity"]
    if last:
        if last["type"] == "activy":
            detail = last.get("description") or "Activy"
        else:
            detail = f"{format_amount(last['amount'])} DZD" if last.get("amount") else (last.get("description") or "")
        line = get_text(
            "dash_last_activity_line", lang,
            time=format_datetime(last["created_at"]),
            operator=_format_operator(last["operator"]),
            phone=last["phone_masked"],
            detail=detail,
        )
        last_activity = get_text("dash_last_activity_title", lang) + line
    else:
        last_activity = get_text("dash_last_activity_none", lang)

    today = summary["today_stats"]
    return get_text(
        "dash_home_title", lang,
        score=str(score), score_emoji=_health_emoji(score),
        reasons=reasons_text,
        total_users=str(summary["total_users"]),
        total_tx=str(summary["total_transactions"]),
        processing_tx=str(summary["processing_transactions"]),
        today_success=str(today["success"]), today_failed=str(today["failed"]),
        today_sales=format_amount(today["sales"]),
        action_required=action_required,
        last_activity=last_activity,
    )


async def _show_dashboard_home(message: Message, lang: str, dashboard_service: DashboardService, force: bool = False) -> None:
    if force:
        await dashboard_service.refresh_diagnostics(force=True)
    summary = await dashboard_service.get_home_summary()
    await message.edit_text(_render_home_text(lang, summary), reply_markup=dashboard_home_keyboard(lang))


def _render_breakdown(lang: str, counts: dict) -> str:
    if not counts:
        return get_text("dash_stats_breakdown_empty", lang)
    return "\n".join(
        get_text("dash_stats_breakdown_line", lang, label=k, count=str(v))
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )


async def _show_dashboard_stats(message: Message, lang: str, dashboard_service: DashboardService, period: str) -> None:
    result = await dashboard_service.get_statistics(period)
    stats = result["stats"]
    text = get_text(
        "dash_stats_title", lang,
        label=result["label"],
        success=str(stats["success"]), failed=str(stats["failed"]),
        sales=format_amount(stats["sales"]),
        new_users=str(result["new_users"]), new_favorites=str(result["new_favorites"]),
        by_operator=_render_breakdown(lang, stats["by_operator"]),
        by_type=_render_breakdown(lang, stats["by_type"]),
    )
    await message.edit_text(text, reply_markup=dashboard_stats_keyboard(lang, period))


async def _show_dashboard_diagnostics(message: Message, lang: str, dashboard_service: DashboardService, force: bool = False) -> None:
    result = await dashboard_service.get_diagnostics(force=force)
    diag = result["diagnostics"]
    failure = result["failure"]

    reachable_text = get_text("dash_status_online", lang) if diag["reachable"] else get_text(
        "dash_status_offline", lang, error=diag["error_message"] or "?"
    )
    wallet_text = f"{diag['wallet_balance']:.2f}" if diag["wallet_balance"] is not None else "—"
    avg_text = f"{diag['avg_response_ms']:.0f} ms" if diag["avg_response_ms"] is not None else "—"
    offer_lines = "\n".join(
        get_text("dash_stats_breakdown_line", lang, label=op, count=str(cnt))
        for op, cnt in diag["offer_counts"].items()
    )
    last_checked = format_datetime(
        datetime.fromtimestamp(diag["last_checked_ts"]).strftime("%Y-%m-%d %H:%M:%S")
    ) if diag.get("last_checked_ts") else "—"

    text = get_text(
        "dash_diagnostics_title", lang,
        reachable=reachable_text, wallet=wallet_text, avg_response=avg_text,
        offer_counts=offer_lines or "—",
        hours=str(dashboard_service.config.FAILURE_RATE_WINDOW_HOURS),
        failure_pct=f"{failure['failure_pct']:.1f}", failed=str(failure["failed"]), total=str(failure["total"]),
        last_checked=last_checked,
    )
    await message.edit_text(text, reply_markup=dashboard_diagnostics_keyboard(lang))


async def _show_dashboard_alerts(message: Message, lang: str, dashboard_service: DashboardService, force: bool = False) -> None:
    if force:
        await dashboard_service.refresh_diagnostics(force=True)
    alerts = await dashboard_service.get_active_alerts()
    if not alerts:
        alerts_text = get_text("dash_alerts_none", lang)
    else:
        lines = []
        for a in alerts:
            key = "dash_alert_line_critical" if a["severity"] == "critical" else "dash_alert_line_warning"
            lines.append(get_text(key, lang, message=a["message"]))
        alerts_text = "\n".join(lines)
    text = get_text("dash_alerts_title", lang, alerts=alerts_text)
    await message.edit_text(text, reply_markup=dashboard_alerts_keyboard(lang))


@router.callback_query(DashboardCallback.filter())
async def dashboard_callback(
    query: CallbackQuery,
    callback_data: DashboardCallback,
    db: Database,
    config: Config,
    dashboard_service: DashboardService,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    screen = callback_data.screen
    force = callback_data.action == "refresh"

    try:
        if screen == "home":
            await _show_dashboard_home(query.message, lang, dashboard_service, force=force)
        elif screen == "stats":
            period = callback_data.value or "today"
            await _show_dashboard_stats(query.message, lang, dashboard_service, period)
        elif screen == "diagnostics":
            await _show_dashboard_diagnostics(query.message, lang, dashboard_service, force=force)
        elif screen == "alerts":
            await _show_dashboard_alerts(query.message, lang, dashboard_service, force=force)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            # Refresh succeeded (cache/diagnostics were re-fetched) but the
            # rendered text happened to be identical to what's on screen —
            # Telegram rejects the no-op edit. Treat as a successful refresh.
            if force:
                await query.answer(get_text("dash_refreshed", lang))
            else:
                await query.answer()
            return
        logger.error("Dashboard render failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return
    except Exception as exc:
        logger.error("Dashboard render failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


# ---------------------------------------------------------------------------
# Operations Center (read-only live ops view — see ops_center.py)
# ---------------------------------------------------------------------------

def _ops_pill_emoji(pill: str) -> str:
    return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(pill, "🟢")


def _fmt_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    return f"{seconds:.0f}s"


async def _show_ops_center_home(message: Message, lang: str, ops_service: OperationsCenterService, force: bool = False) -> None:
    summary = await ops_service.get_home_summary(force=force)
    queue = summary["queue"]
    throughput = summary["throughput"]
    oneclick = summary["oneclick"]

    reachable_text = get_text("dash_status_online", lang) if oneclick["reachable"] else get_text(
        "dash_status_offline", lang, error="—"
    )
    wallet_text = f"{oneclick['wallet_balance']:.2f}" if oneclick["wallet_balance"] is not None else "—"
    oldest_text = _fmt_seconds(queue["oldest_age_seconds"])
    avg_text = _fmt_seconds(throughput["avg_completion_seconds"])
    last_checked = format_datetime(
        datetime.fromtimestamp(oneclick["last_checked_ts"]).strftime("%Y-%m-%d %H:%M:%S")
    ) if oneclick.get("last_checked_ts") else "—"

    text = get_text(
        "ops_home_title", lang,
        pill=_ops_pill_emoji(summary["pill"]),
        reachable=reachable_text, wallet=wallet_text,
        processing_count=str(queue["processing_count"]),
        slow_count=str(queue["slow_count"]), threshold=str(queue["threshold_seconds"]),
        oldest_age=oldest_text,
        completed_1h=str(throughput["completed_1h"]),
        success_1h=str(throughput["success_1h"]), failed_1h=str(throughput["failed_1h"]),
        avg_completion=avg_text,
        last_checked=last_checked,
    )
    await message.edit_text(text, reply_markup=ops_center_home_keyboard(lang))


async def _show_ops_processing_list(message: Message, lang: str, ops_service: OperationsCenterService) -> None:
    txns = await ops_service.get_processing_list()
    header = get_text("ops_processing_title", lang, count=str(len(txns)))
    body = header if txns else header + "\n" + get_text("ops_list_empty", lang)
    await message.edit_text(body, reply_markup=ops_list_keyboard(txns, "processing", lang))


async def _show_ops_completed_list(message: Message, lang: str, ops_service: OperationsCenterService) -> None:
    txns = await ops_service.get_completed_list()
    header = get_text("ops_completed_title", lang, count=str(len(txns)))
    body = header if txns else header + "\n" + get_text("ops_list_empty", lang)
    await message.edit_text(body, reply_markup=ops_list_keyboard(txns, "completed", lang))


async def _show_ops_tx_detail(message: Message, lang: str, db: Database, tx_id: int) -> None:
    tx = await db.get_transaction_admin(tx_id)
    if not tx:
        await message.edit_text(get_text("history_not_found", lang), reply_markup=ops_center_home_keyboard(lang))
        return
    await message.edit_text(
        await _history_details_text(tx, lang),
        reply_markup=ops_tx_detail_keyboard(tx, lang),
    )


@router.callback_query(OpsCallback.filter())
async def ops_center_callback(
    query: CallbackQuery,
    callback_data: OpsCallback,
    db: Database,
    config: Config,
    ops_center_service: OperationsCenterService,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    screen = callback_data.screen
    force = callback_data.action == "refresh"

    try:
        if screen == "home":
            await _show_ops_center_home(query.message, lang, ops_center_service, force=force)
        elif screen == "processing":
            await _show_ops_processing_list(query.message, lang, ops_center_service)
        elif screen == "completed":
            await _show_ops_completed_list(query.message, lang, ops_center_service)
        elif screen == "tx_detail":
            if callback_data.action == "copy_ref":
                tx = await db.get_transaction_admin(int(callback_data.value))
                await query.message.answer(f"<code>{tx.get('reference') or ''}</code>" if tx else "")
                await query.answer(get_text("history_ref_copied", lang))
                return
            await _show_ops_tx_detail(query.message, lang, db, int(callback_data.value))
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            if force:
                await query.answer(get_text("dash_refreshed", lang))
            else:
                await query.answer()
            return
        logger.error("Operations Center render failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return
    except Exception as exc:
        logger.error("Operations Center render failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


# ---------------------------------------------------------------------------
# Admin FSM helpers
# ---------------------------------------------------------------------------

async def _fsm_add_balance_id(
    message: Message, text: str, db: Database,
    config: Config, state: FSMContext, lang: str,
) -> None:
    if not text.isdigit():
        await message.answer(get_text("admin_invalid_id", lang))
        return
    target_id = int(text)
    target = await db.get_user(target_id)
    if not target:
        await message.answer(get_text("admin_user_not_found_retry", lang, uid=str(target_id)))
        await state.clear()
        return
    await state.update_data(target_telegram_id=target_id)
    await state.set_state(AdminStates.waiting_add_balance_amount)
    await message.answer(
        get_text("admin_add_balance_ask_amount", lang,
                 name=target["full_name"] or str(target_id),
                 balance=f"{target['balance']:.0f}")
    )


async def _fsm_add_balance_amount(
    message: Message, text: str, db: Database,
    config: Config, state: FSMContext, lang: str,
) -> None:
    data = await state.get_data()
    target_id = data.get("target_telegram_id")
    if not target_id:
        await state.clear()
        return
    try:
        amount = float(text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(get_text("admin_invalid_amount", lang))
        return
    new_balance = await db.adjust_balance(target_id, amount)
    await db.log("admin_add_balance", f"target={target_id} amount={amount}")
    await state.clear()
    await message.answer(
        get_text("admin_balance_added", lang,
                 amount=f"{amount:.0f}",
                 uid=str(target_id),
                 balance=f"{new_balance:.0f}"),
        reply_markup=admin_keyboard(lang, config.MOCK_MODE),
    )


async def _fsm_sub_balance_id(
    message: Message, text: str, db: Database,
    config: Config, state: FSMContext, lang: str,
) -> None:
    if not text.isdigit():
        await message.answer(get_text("admin_invalid_id", lang))
        return
    target_id = int(text)
    target = await db.get_user(target_id)
    if not target:
        await message.answer(get_text("admin_user_not_found_retry", lang, uid=str(target_id)))
        await state.clear()
        return
    await state.update_data(target_telegram_id=target_id)
    await state.set_state(AdminStates.waiting_sub_balance_amount)
    await message.answer(
        get_text("admin_sub_balance_ask_amount", lang,
                 name=target["full_name"] or str(target_id),
                 balance=f"{target['balance']:.0f}")
    )


async def _fsm_sub_balance_amount(
    message: Message, text: str, db: Database,
    config: Config, state: FSMContext, lang: str,
) -> None:
    data = await state.get_data()
    target_id = data.get("target_telegram_id")
    if not target_id:
        await state.clear()
        return
    try:
        amount = float(text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(get_text("admin_invalid_amount", lang))
        return
    new_balance = await db.adjust_balance(target_id, -amount)
    await db.log("admin_sub_balance", f"target={target_id} amount={amount}")
    await state.clear()
    await message.answer(
        get_text("admin_balance_subtracted", lang,
                 amount=f"{amount:.0f}",
                 uid=str(target_id),
                 balance=f"{new_balance:.0f}"),
        reply_markup=admin_keyboard(lang, config.MOCK_MODE),
    )


async def _fsm_broadcast(
    message: Message, text: str, db: Database,
    config: Config, state: FSMContext, lang: str,
) -> None:
    users = await db.get_all_users()
    bot: Bot = message.bot  # type: ignore[assignment]
    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u["telegram_id"], text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
    await state.clear()
    await db.log("admin_broadcast", f"sent={sent} failed={failed}")
    await message.answer(
        get_text("admin_broadcast_done", lang, sent=str(sent), failed=str(failed)),
        reply_markup=admin_keyboard(lang, config.MOCK_MODE),
    )


# ---------------------------------------------------------------------------
# Distributor Management — Admin callbacks (Phase 1: Foundation)
#
# Read/write scoped strictly to the new `distributors` table via
# DistributorService. Never touches RechargeService/OneClickAPI/WalletService,
# wallet balances, or transaction logic. wallet_balance is display-only here.
# ---------------------------------------------------------------------------

def _distributor_detail_text(d: Dict[str, Any], lang: str) -> str:
    status_key = "dadm_status_active" if d["status"] == "active" else "dadm_status_suspended"
    last_activity = format_datetime(d["last_activity_at"]) if d.get("last_activity_at") else get_text("dadm_no_activity", lang)
    return get_text(
        "dadm_detail", lang,
        name=d["full_name"],
        telegram_id=str(d["telegram_id"]),
        phone=d["phone"] or "—",
        balance=format_amount(d["wallet_balance"]),
        status=get_text(status_key, lang),
        last_activity=last_activity,
        created_at=format_datetime(d["created_at"]),
    )


async def _show_distributor_list(
    message: Message, lang: str, distributor_service: DistributorService,
    config: Config, page: int, search_query: Optional[str] = None,
) -> None:
    page_size = config.DISTRIBUTOR_LIST_PAGE_SIZE
    offset = page * page_size
    if search_query:
        items = await distributor_service.search(search_query, limit=page_size, offset=offset)
        total = await distributor_service.count_search(search_query)
    else:
        items = await distributor_service.list(limit=page_size, offset=offset)
        total = await distributor_service.count()

    total_pages = max(1, (total + page_size - 1) // page_size)

    if total == 0:
        empty_key = "dadm_search_empty" if search_query else "dadm_list_empty"
        await message.answer(get_text(empty_key, lang), reply_markup=distributor_admin_menu_keyboard(lang))
        return

    await message.answer(
        get_text("dadm_list_title", lang, count=str(total)),
        reply_markup=distributor_list_keyboard(items, page, total_pages, lang, search=bool(search_query)),
    )


@router.callback_query(DistributorAdminCallback.filter())
async def distributor_admin_callback(
    query: CallbackQuery,
    callback_data: DistributorAdminCallback,
    db: Database,
    config: Config,
    distributor_service: DistributorService,
    state: FSMContext,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    action = callback_data.action

    try:
        if action == "menu":
            await state.clear()
            await query.message.answer(get_text("dadm_menu_title", lang), reply_markup=distributor_admin_menu_keyboard(lang))

        elif action == "create":
            await state.set_state(DistributorAdminStates.waiting_create_telegram_id)
            await query.message.answer(get_text("dadm_ask_telegram_id", lang))

        elif action == "search":
            await state.set_state(DistributorAdminStates.waiting_search_query)
            await query.message.answer(get_text("dadm_ask_search", lang))

        elif action == "list":
            await _show_distributor_list(query.message, lang, distributor_service, config, page=0)

        elif action == "page":
            data = await state.get_data()
            search_query = data.get("distributor_search_query")
            await _show_distributor_list(query.message, lang, distributor_service, config, page=callback_data.page, search_query=search_query)

        elif action == "detail":
            d = await distributor_service.get(callback_data.distributor_id)
            if not d:
                await query.answer(get_text("dadm_not_found", lang), show_alert=True)
                return
            await query.message.answer(
                _distributor_detail_text(d, lang),
                reply_markup=distributor_detail_keyboard(d, callback_data.page, lang),
            )

        elif action in ("activate", "suspend"):
            new_status = "active" if action == "activate" else "suspended"
            await distributor_service.set_status(callback_data.distributor_id, new_status)
            d = await distributor_service.get(callback_data.distributor_id)
            await query.answer(get_text("dadm_status_updated", lang))
            if d:
                await query.message.answer(
                    _distributor_detail_text(d, lang),
                    reply_markup=distributor_detail_keyboard(d, callback_data.page, lang),
                )
            return
    except Exception as exc:
        logger.error("Distributor admin action failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


async def _fsm_distributor_create_telegram_id(
    message: Message, text: str, state: FSMContext, lang: str,
) -> None:
    if not text.isdigit():
        await message.answer(get_text("dadm_invalid_telegram_id", lang))
        return
    await state.update_data(new_distributor_telegram_id=int(text))
    await state.set_state(DistributorAdminStates.waiting_create_full_name)
    await message.answer(get_text("dadm_ask_full_name", lang))


async def _fsm_distributor_create_full_name(
    message: Message, text: str, state: FSMContext, lang: str,
) -> None:
    await state.update_data(new_distributor_full_name=text)
    await state.set_state(DistributorAdminStates.waiting_create_phone)
    await message.answer(get_text("dadm_ask_phone", lang))


async def _fsm_distributor_create_phone(
    message: Message, text: str, db: Database, config: Config,
    distributor_service: DistributorService, state: FSMContext, lang: str,
) -> None:
    data = await state.get_data()
    telegram_id = data.get("new_distributor_telegram_id")
    full_name = data.get("new_distributor_full_name")
    if not telegram_id or not full_name:
        await state.clear()
        return
    phone = None if text.strip() == "-" else text.strip()

    target_user = await db.get_user(telegram_id)
    username = target_user["username"] if target_user else None

    try:
        created = await distributor_service.create(
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
            phone=phone,
            created_by_admin_id=message.from_user.id,
        )
    except ValueError:
        await message.answer(get_text("dadm_already_exists", lang))
        await state.clear()
        return

    await state.clear()
    await message.answer(
        get_text("dadm_created", lang, name=created["full_name"], telegram_id=str(created["telegram_id"]), phone=created["phone"] or "—"),
        reply_markup=distributor_admin_menu_keyboard(lang),
    )


async def _fsm_distributor_search_query(
    message: Message, text: str, config: Config,
    distributor_service: DistributorService, state: FSMContext, lang: str,
) -> None:
    await state.update_data(distributor_search_query=text)
    await state.clear()
    await _show_distributor_list(message, lang, distributor_service, config, page=0, search_query=text)


# ---------------------------------------------------------------------------
# Transaction History callbacks
#
# Read-only except "repeat" and "add_favorite", which only ever hand data to
# the existing, unmodified recharge/Activy/favorites entry points
# (confirm_recharge_keyboard / _show_activy_offers / add_favorite). No
# recharge, OneClick, or wallet logic is duplicated here.
# ---------------------------------------------------------------------------

async def _history_details_text(tx: dict, lang: str) -> str:
    op_key = (tx.get("operator") or "").lower()
    if not op_key and tx.get("phone"):
        op_key = OperatorDetector.detect(tx["phone"])
    operator = OperatorDetector.label(op_key, lang) if op_key and op_key != "unknown" else "—"
    return get_text(
        "history_details", lang,
        phone=tx["phone"] or "-",
        favorite=tx.get("favorite_label") or get_text("history_no_favorite", lang),
        operator=operator,
        type=get_text(f"history_type_{tx['type']}", lang),
        amount=f"{tx['amount']:,.0f}",
        date=format_datetime(tx["created_at"]),
        reference=tx.get("reference") or get_text("history_no_reference", lang),
        status=tx_status_label(tx["status"], lang),
    )


@router.callback_query(HistoryNavCallback.filter())
async def history_nav_callback(
    query: CallbackQuery,
    callback_data: HistoryNavCallback,
    db: Database,
    config: Config,
    state: FSMContext,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    data = await state.get_data()
    search = data.get("history_search")
    date_filter = data.get("history_date_filter")
    status_filter = data.get("history_status_filter")
    action = callback_data.action

    if action == "page":
        await _show_history_list(query.message, db, db_user["id"], lang, callback_data.page,
                                  search, date_filter, status_filter, config, edit=True)
    elif action == "filter_menu":
        await query.message.edit_text(
            get_text("history_filter_title", lang),
            reply_markup=history_filter_keyboard(date_filter, status_filter, lang),
        )
    elif action == "filter_set":
        value = callback_data.value
        if value in ("today", "yesterday", "7days"):
            date_filter = None if date_filter == value else value
            await state.update_data(history_date_filter=date_filter)
        elif value in ("success", "failed"):
            status_filter = None if status_filter == value else value
            await state.update_data(history_status_filter=status_filter)
        await query.message.edit_text(
            get_text("history_filter_title", lang),
            reply_markup=history_filter_keyboard(date_filter, status_filter, lang),
        )
    elif action == "clear_filter":
        await state.update_data(history_date_filter=None, history_status_filter=None)
        await _show_history_list(query.message, db, db_user["id"], lang, 0,
                                  search, None, None, config, edit=True)
    elif action == "search":
        await state.set_state(HistoryStates.waiting_search_query)
        await query.message.edit_text(get_text("history_ask_search", lang))
    elif action == "clear_search":
        await state.update_data(history_search=None)
        await _show_history_list(query.message, db, db_user["id"], lang, 0,
                                  None, date_filter, status_filter, config, edit=True)
    elif action == "back_to_list":
        await _show_history_list(query.message, db, db_user["id"], lang, 0,
                                  search, date_filter, status_filter, config, edit=True)

    await query.answer()


@router.callback_query(HistorySelectCallback.filter())
async def history_select_callback(
    query: CallbackQuery,
    callback_data: HistorySelectCallback,
    db: Database,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    tx = await db.get_transaction(callback_data.tx_id, db_user["id"])
    if not tx:
        await query.answer(get_text("history_not_found", lang), show_alert=True)
        return

    is_favorited = bool(tx.get("favorite_label"))
    await query.message.edit_text(
        await _history_details_text(tx, lang),
        reply_markup=history_details_keyboard(tx, callback_data.page, is_favorited, lang),
    )
    await query.answer()


@router.callback_query(HistoryActionCallback.filter())
async def history_action_callback(
    query: CallbackQuery,
    callback_data: HistoryActionCallback,
    db: Database,
    config: Config,
    state: FSMContext,
    api: OneClickAPI,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    tx = await db.get_transaction(callback_data.tx_id, db_user["id"])
    if not tx:
        await query.answer(get_text("history_not_found", lang), show_alert=True)
        return

    action = callback_data.action

    if action == "back":
        data = await state.get_data()
        await _show_history_list(
            query.message, db, db_user["id"], lang, callback_data.page,
            data.get("history_search"), data.get("history_date_filter"), data.get("history_status_filter"),
            config, edit=True,
        )

    elif action == "copy_ref":
        await query.message.answer(f"<code>{tx.get('reference') or ''}</code>")
        await query.answer(get_text("history_ref_copied", lang))
        return

    elif action == "add_favorite":
        existing = await db.get_favorite_by_phone(db_user["id"], tx["phone"])
        if existing:
            await query.answer(get_text("favorites_already_saved", lang, label=existing["label"]), show_alert=True)
            return
        await state.update_data(history_favorite_tx_id=tx["id"], history_favorite_page=callback_data.page)
        await state.set_state(HistoryStates.waiting_favorite_label)
        await query.message.edit_text(get_text("favorites_ask_label", lang))

    elif action == "repeat":
        if tx["type"] == "standard":
            operator = OperatorDetector.detect(tx["phone"])
            if operator == "unknown":
                await query.message.edit_text(
                    get_text("activy_choose_operator", lang, phone=tx["phone"]),
                    reply_markup=std_operator_choice_keyboard(tx["phone"], int(tx["amount"]), lang),
                )
            else:
                op_label = OperatorDetector.label(operator, lang)
                token = secrets.token_hex(8)
                await query.message.edit_text(
                    get_text("recharge_confirm", lang, phone=tx["phone"], amount=str(int(tx["amount"])), operator=op_label),
                    reply_markup=confirm_recharge_keyboard(tx["phone"], int(tx["amount"]), lang, token=token),
                )
            await db.log("recharge_intent", f"phone={tx['phone']} amount={tx['amount']} op={operator} via=history_repeat", db_user["id"])
        elif tx["type"] == "activy":
            operator = OperatorDetector.detect(tx["phone"])
            if operator == "unknown":
                await query.message.edit_text(
                    get_text("activy_choose_operator", lang, phone=tx["phone"]),
                    reply_markup=activy_operator_choice_keyboard(tx["phone"], lang),
                )
            else:
                await query.message.delete()
                await _show_activy_offers(query.message, tx["phone"], operator, lang, db, db_user["id"], api)
        else:
            await query.answer(get_text("history_repeat_unsupported", lang), show_alert=True)
            return

    await query.answer()


async def _fsm_history_search_query(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str, config: Config,
) -> None:
    query_text = text.strip()
    await state.update_data(history_search=query_text)
    await state.set_state(None)
    data = await state.get_data()
    await _show_history_list(
        message, db, user_id, lang, 0, query_text,
        data.get("history_date_filter"), data.get("history_status_filter"), config,
    )


async def _fsm_history_favorite_label(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str, config: Config,
) -> None:
    label = text.strip()[:50]
    if not label:
        await message.answer(get_text("favorites_ask_label", lang))
        return
    data = await state.get_data()
    tx_id = data.get("history_favorite_tx_id")
    await state.set_state(None)

    tx = await db.get_transaction(tx_id, user_id) if tx_id else None
    if not tx:
        await message.answer(get_text("history_not_found", lang))
        return

    existing = await db.get_favorite_by_phone(user_id, tx["phone"])
    if existing:
        await message.answer(get_text("favorites_already_saved", lang, label=existing["label"]))
        return

    count = await db.count_favorites(user_id)
    if count >= config.FAVORITES_LIMIT:
        await message.answer(get_text("favorites_limit_reached", lang, limit=str(config.FAVORITES_LIMIT)))
        return

    await db.add_favorite(user_id, label, tx["phone"])
    await db.log("favorite_added", f"phone={tx['phone']} label={label} via=history", user_id)
    await message.answer(get_text("favorites_added", lang, label=label, phone=tx["phone"]))


# ---------------------------------------------------------------------------
# Favorite Numbers (address book) callbacks
#
# All of these are purely presentational bridges: they save/read the phone
# number and then hand off to the existing, unmodified recharge/Activy entry
# points (confirm_recharge_keyboard / _show_activy_offers /
# activy_operator_choice_keyboard / std_operator_choice_keyboard). No
# recharge, OneClick, or wallet logic is duplicated here.
# ---------------------------------------------------------------------------

@router.callback_query(FavoriteMenuCallback.filter())
async def favorite_menu_callback(
    query: CallbackQuery,
    callback_data: FavoriteMenuCallback,
    db: Database,
    config: Config,
    state: FSMContext,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    await state.clear()

    if callback_data.action == "list":
        await _show_favorites_list(query.message, db, db_user["id"], lang, edit=True)
    elif callback_data.action == "add":
        count = await db.count_favorites(db_user["id"])
        if count >= config.FAVORITES_LIMIT:
            await query.answer(get_text("favorites_limit_reached", lang, limit=str(config.FAVORITES_LIMIT)), show_alert=True)
            return
        await state.set_state(FavoriteStates.waiting_add_phone)
        await query.message.edit_text(get_text("favorites_ask_phone", lang))
    elif callback_data.action == "search":
        await state.set_state(FavoriteStates.waiting_search_query)
        await query.message.edit_text(get_text("favorites_ask_search", lang))

    await query.answer()


@router.callback_query(FavoriteSelectCallback.filter())
async def favorite_select_callback(
    query: CallbackQuery,
    callback_data: FavoriteSelectCallback,
    db: Database,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    fav = await db.get_favorite(callback_data.favorite_id, db_user["id"])
    if not fav:
        await query.answer(get_text("favorites_not_found", lang), show_alert=True)
        return

    operator = OperatorDetector.detect(fav["phone"])
    await query.message.edit_text(
        get_text("favorites_entry_line", lang, label=fav["label"], phone=fav["phone"],
                 operator=OperatorDetector.label(operator, lang)),
        reply_markup=favorite_actions_keyboard(fav["id"], lang),
    )
    await query.answer()


@router.callback_query(FavoriteActionCallback.filter())
async def favorite_action_callback(
    query: CallbackQuery,
    callback_data: FavoriteActionCallback,
    db: Database,
    config: Config,
    state: FSMContext,
    api: OneClickAPI,
) -> None:
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]
    fav = await db.get_favorite(callback_data.favorite_id, db_user["id"])
    if not fav:
        await query.answer(get_text("favorites_not_found", lang), show_alert=True)
        return

    action = callback_data.action

    if action == "back":
        await _show_favorites_list(query.message, db, db_user["id"], lang, edit=True)

    elif action == "recharge":
        await state.set_state(FavoriteStates.waiting_recharge_amount)
        await state.update_data(favorite_id=fav["id"])
        await query.message.edit_text(
            get_text("favorites_ask_amount", lang, label=fav["label"], phone=fav["phone"])
        )

    elif action == "activy":
        operator = OperatorDetector.detect(fav["phone"])
        await db.touch_favorite(fav["id"], db_user["id"])
        if operator == "unknown":
            await query.message.edit_text(
                get_text("activy_choose_operator", lang, phone=fav["phone"]),
                reply_markup=activy_operator_choice_keyboard(fav["phone"], lang),
            )
        else:
            await query.message.delete()
            await _show_activy_offers(query.message, fav["phone"], operator, lang, db, db_user["id"], api)

    elif action == "rename":
        await state.set_state(FavoriteStates.waiting_rename_label)
        await state.update_data(favorite_id=fav["id"])
        await query.message.edit_text(get_text("favorites_ask_rename", lang, label=fav["label"]))

    elif action == "delete":
        await query.message.edit_text(
            get_text("favorites_delete_confirm", lang, label=fav["label"], phone=fav["phone"]),
            reply_markup=favorite_delete_confirm_keyboard(fav["id"], lang),
        )

    elif action == "delete_confirm":
        await db.delete_favorite(fav["id"], db_user["id"])
        await query.message.edit_text(get_text("favorites_deleted", lang, label=fav["label"]))
        await db.log("favorite_deleted", f"favorite_id={fav['id']}", db_user["id"])

    await query.answer()


# ---------------------------------------------------------------------------
# Favorite Numbers FSM helpers
# ---------------------------------------------------------------------------

async def _fsm_favorite_add_phone(
    message: Message, text: str, db: Database, state: FSMContext, lang: str,
) -> None:
    phone = text.strip()
    if not is_algerian_phone(phone):
        await message.answer(get_text("favorites_invalid_phone", lang))
        return
    await state.update_data(phone=phone)
    await state.set_state(FavoriteStates.waiting_add_label)
    await message.answer(get_text("favorites_ask_label", lang))


async def _fsm_favorite_add_label(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str, config: Config,
) -> None:
    label = text.strip()[:50]
    if not label:
        await message.answer(get_text("favorites_ask_label", lang))
        return
    data = await state.get_data()
    phone = data.get("phone")
    await state.clear()

    existing = await db.get_favorite_by_phone(user_id, phone)
    if existing:
        await message.answer(get_text("favorites_already_saved", lang, label=existing["label"]))
        return

    count = await db.count_favorites(user_id)
    if count >= config.FAVORITES_LIMIT:
        await message.answer(get_text("favorites_limit_reached", lang, limit=str(config.FAVORITES_LIMIT)))
        return

    await db.add_favorite(user_id, label, phone)
    await db.log("favorite_added", f"phone={phone} label={label}", user_id)
    await message.answer(get_text("favorites_added", lang, label=label, phone=phone))
    await _show_favorites_list(message, db, user_id, lang)


async def _fsm_favorite_rename_label(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str,
) -> None:
    label = text.strip()[:50]
    if not label:
        await message.answer(get_text("favorites_ask_label", lang))
        return
    data = await state.get_data()
    favorite_id = data.get("favorite_id")
    await state.clear()

    fav = await db.get_favorite(favorite_id, user_id) if favorite_id else None
    if not fav:
        await message.answer(get_text("favorites_not_found", lang))
        return

    await db.rename_favorite(favorite_id, user_id, label)
    await db.log("favorite_renamed", f"favorite_id={favorite_id} label={label}", user_id)
    await message.answer(get_text("favorites_renamed", lang, label=label))


async def _fsm_favorite_search_query(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str,
) -> None:
    query_text = text.strip()
    await state.clear()
    results = await db.search_favorites(user_id, query_text)
    if not results:
        await message.answer(get_text("favorites_search_empty", lang, query=query_text))
        return
    await message.answer(
        get_text("favorites_search_results", lang, query=query_text, entries=_build_favorites_text(results, lang)),
        reply_markup=favorites_list_keyboard(results, lang),
    )


async def _fsm_favorite_recharge_amount(
    message: Message, text: str, db: Database, user_id: int,
    state: FSMContext, lang: str,
) -> None:
    data = await state.get_data()
    favorite_id = data.get("favorite_id")
    fav = await db.get_favorite(favorite_id, user_id) if favorite_id else None
    if not fav:
        await state.clear()
        await message.answer(get_text("favorites_not_found", lang))
        return

    if not text.strip().isdigit():
        await message.answer(get_text("favorites_ask_amount", lang, label=fav["label"], phone=fav["phone"]))
        return
    amount = int(text.strip())
    if amount < 10 or amount > 5000:
        await message.answer(get_text("favorites_ask_amount", lang, label=fav["label"], phone=fav["phone"]))
        return

    await state.clear()
    phone = fav["phone"]
    operator = OperatorDetector.detect(phone)
    await db.touch_favorite(favorite_id, user_id)

    if operator == "unknown":
        await message.answer(
            get_text("activy_choose_operator", lang, phone=phone),
            reply_markup=std_operator_choice_keyboard(phone, amount, lang),
        )
        return

    op_label = OperatorDetector.label(operator, lang)
    token = secrets.token_hex(8)
    await message.answer(
        get_text("recharge_confirm", lang, phone=phone, amount=str(amount), operator=op_label),
        reply_markup=confirm_recharge_keyboard(phone, amount, lang, token=token),
    )
    await db.log("recharge_intent", f"phone={phone} amount={amount} op={operator} via=favorite", user_id)


# ---------------------------------------------------------------------------
# Distributor Wallet & Ledger — Phase 2 callbacks + FSM helpers
#
# Admin-only.  Never touches RechargeService / OneClickAPI / WalletService /
# RechargeTracker or any customer recharge/Activy code path.
# ---------------------------------------------------------------------------

@router.callback_query(DistributorWalletCallback.filter())
async def distributor_wallet_callback(
    query: CallbackQuery,
    callback_data: DistributorWalletCallback,
    db: Database,
    config: Config,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
    state: FSMContext,
) -> None:
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    action    = callback_data.action
    dist_id   = callback_data.distributor_id

    try:
        if action == "menu":
            d = await distributor_service.get(dist_id)
            if not d:
                await query.answer(get_text("dwlt_err_not_found", lang), show_alert=True)
                return
            await query.message.edit_text(
                get_text("dwlt_menu_title", lang,
                         name=d["full_name"],
                         balance=format_amount_ledger(d["wallet_balance"])),
                reply_markup=distributor_wallet_menu_keyboard(d, lang),
            )

        elif action in ("ask_credit", "ask_debit"):
            op = "credit" if action == "ask_credit" else "debit"
            await state.set_state(DistributorWalletStates.waiting_amount)
            await state.update_data(
                dwlt_distributor_id=dist_id,
                dwlt_op=op,
                dwlt_prompt_message_id=query.message.message_id,
                dwlt_prompt_chat_id=query.message.chat.id,
            )
            key = "dwlt_ask_credit_amount" if op == "credit" else "dwlt_ask_debit_amount"
            await query.message.edit_text(get_text(key, lang))

        elif action == "confirm":
            amount = callback_data.amount_cents / 100
            op = callback_data.op

            # ── Idempotency guard ──────────────────────────────────────────
            # Strip the Confirm/Cancel keyboard atomically BEFORE any DB write.
            # If Telegram replies "message is not modified" it means a previous
            # tap already stripped it — this is a duplicate; drop it silently.
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    await query.answer()
                    return
                raise
            # ─────────────────────────────────────────────────────────────

            d = await distributor_service.get(dist_id)
            if not d:
                await query.answer(get_text("dwlt_err_not_found", lang), show_alert=True)
                return
            if op == "credit":
                entry = await distributor_wallet_service.credit(
                    dist_id, amount, created_by=user.id
                )
                key = "dwlt_success_credit"
            else:
                entry = await distributor_wallet_service.debit(
                    dist_id, amount, created_by=user.id
                )
                key = "dwlt_success_debit"
            await state.clear()
            new_balance = entry["balance_after"]
            updated_d = {**d, "wallet_balance": new_balance}
            await query.message.edit_text(
                get_text(key, lang,
                         name=d["full_name"],
                         amount=format_amount_ledger(amount),
                         balance=format_amount_ledger(new_balance)),
                reply_markup=distributor_wallet_menu_keyboard(updated_d, lang),
            )

        elif action == "cancel":
            await state.clear()
            d = await distributor_service.get(dist_id)
            if d:
                await query.message.edit_text(
                    get_text("dwlt_cancelled", lang),
                    reply_markup=distributor_wallet_menu_keyboard(d, lang),
                )

        elif action in ("ledger", "lpage"):
            await _show_dwlt_ledger(
                query.message, lang, dist_id, distributor_service,
                distributor_wallet_service, config, page=callback_data.page, edit=True,
            )

        elif action == "entry":
            await _show_dwlt_entry(
                query.message, lang, callback_data.entry_id,
                callback_data.page, distributor_wallet_service, db=db, edit=True,
            )

    except ValueError as exc:
        err_key = str(exc)
        if err_key == "insufficient_balance":
            d = await distributor_service.get(dist_id)
            cur_bal = d["wallet_balance"] if d else 0.0
            amt = callback_data.amount_cents / 100
            await query.answer(
                get_text("dwlt_err_insufficient", lang,
                         balance=format_amount_ledger(cur_bal),
                         amount=format_amount_ledger(amt)),
                show_alert=True,
            )
            return
        elif err_key == "distributor_suspended":
            await query.answer(get_text("dwlt_err_suspended", lang), show_alert=True)
            return
        else:
            logger.error("Distributor wallet ValueError: %s", exc)
            await query.answer(get_text("error_generic", lang), show_alert=True)
            return
    except Exception as exc:
        logger.exception("Distributor wallet error (action=%s dist_id=%s): %s",
                         action, dist_id, exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


async def _fsm_dwlt_amount(
    message: Message,
    text: str,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
    config: Config,
    state: FSMContext,
    lang: str,
) -> None:
    """FSM handler: admin just entered a credit/debit amount."""
    # Parse
    try:
        amount = float(text.replace(",", ".").replace(" ", "").replace("\u00a0", ""))
        if amount <= 0:
            raise ValueError("non_positive")
    except (ValueError, TypeError):
        await message.answer(get_text("dwlt_invalid_amount", lang))
        return

    fsm_data = await state.get_data()
    dist_id = fsm_data.get("dwlt_distributor_id")
    op      = fsm_data.get("dwlt_op")
    if not dist_id or not op:
        await state.clear()
        return

    d = await distributor_service.get(dist_id)
    if not d:
        await state.clear()
        await message.answer(get_text("dwlt_err_not_found", lang))
        return

    if d["status"] != "active":
        await state.clear()
        await message.answer(get_text("dwlt_err_suspended", lang))
        return

    current_balance: float = d["wallet_balance"]

    # Pre-validate debit won't go negative (give early feedback; DB also enforces it)
    if op == "debit" and amount > current_balance:
        await message.answer(
            get_text("dwlt_err_insufficient", lang,
                     balance=format_amount_ledger(current_balance),
                     amount=format_amount_ledger(amount))
        )
        return

    balance_after = current_balance + amount if op == "credit" else current_balance - amount
    amount_cents  = round(amount * 100)

    confirm_key = "dwlt_confirm_credit" if op == "credit" else "dwlt_confirm_debit"
    confirm_text = get_text(
        confirm_key, lang,
        name=d["full_name"],
        amount=format_amount_ledger(amount),
        before=format_amount_ledger(current_balance),
        after=format_amount_ledger(balance_after),
    )
    confirm_kb = distributor_wallet_confirm_keyboard(dist_id, amount_cents, op, lang)

    # Retrieve the prompt message we stored in FSM state, then clear state
    prompt_message_id = fsm_data.get("dwlt_prompt_message_id")
    prompt_chat_id    = fsm_data.get("dwlt_prompt_chat_id")
    await state.clear()

    # Best-effort: edit the "Enter amount" prompt in-place so the conversation
    # stays as a single message thread.  Fall back to a new message if Telegram
    # rejects the edit (e.g. message too old, already deleted, etc.).
    if prompt_message_id and prompt_chat_id:
        try:
            await message.bot.edit_message_text(
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                text=confirm_text,
                reply_markup=confirm_kb,
            )
            return
        except Exception:
            pass  # fall through to answer()

    await message.answer(confirm_text, reply_markup=confirm_kb)


# ---------------------------------------------------------------------------
# Distributor self-service wallet & ledger helpers (Phase 3B)
# ---------------------------------------------------------------------------

async def _show_dist_self_wallet(
    message: Message,
    dist: Dict[str, Any],
    wallet_service: DistributorWalletService,
    lang: str,
    edit: bool = False,
) -> None:
    """Show a distributor their own wallet balance screen."""
    balance = await wallet_service.get_balance(dist["id"]) or 0.0
    text = get_text(
        "dist_self_wallet_title", lang,
        balance=format_amount(balance),
    )
    _send = message.edit_text if edit else message.answer
    await _send(text, reply_markup=dist_self_wallet_keyboard(lang))


async def _show_dist_self_ledger(
    message: Message,
    distributor_id: int,
    wallet_service: DistributorWalletService,
    config: Config,
    lang: str,
    page: int = 0,
    edit: bool = False,
) -> None:
    """Show a distributor their own paginated ledger."""
    page_size   = config.DISTRIBUTOR_LEDGER_PAGE_SIZE
    total       = await wallet_service.count_ledger(distributor_id)
    total_pages = max(1, (total + page_size - 1) // page_size)
    _send = message.edit_text if edit else message.answer

    if total == 0:
        await _send(
            get_text("dist_self_ledger_empty", lang),
            reply_markup=dist_self_wallet_keyboard(lang),
        )
        return

    entries = await wallet_service.get_ledger(
        distributor_id, limit=page_size, offset=page * page_size,
    )
    await _send(
        get_text("dist_self_ledger_title", lang, count=str(total)),
        reply_markup=dist_self_ledger_keyboard(entries, page, total_pages, lang),
    )


@router.callback_query(DistributorSelfCallback.filter())
async def distributor_self_callback(
    query: CallbackQuery,
    callback_data: DistributorSelfCallback,
    db: Database,
    config: Config,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
) -> None:
    """Handles all distributor-facing self-service wallet/ledger navigation."""
    user = query.from_user
    db_user = await _user(db, user.id)
    if not db_user:
        await query.answer()
        return
    lang = db_user["language"]

    dist = await distributor_service.get_by_telegram_id(user.id)
    if not dist:
        await query.answer(get_text("dist_self_no_account", lang), show_alert=True)
        return

    action = callback_data.action

    try:
        if action == "wallet":
            await _show_dist_self_wallet(query.message, dist, distributor_wallet_service, lang, edit=True)

        elif action in ("ledger", "lpage"):
            await _show_dist_self_ledger(
                query.message, dist["id"], distributor_wallet_service, config, lang,
                page=callback_data.page, edit=True,
            )

        elif action == "entry":
            e = await distributor_wallet_service.get_ledger_entry(callback_data.entry_id)
            if not e:
                await query.answer(get_text("dwlt_err_not_found", lang), show_alert=True)
                return

            op_type = e.get("operation_type", "")
            if op_type == "admin_credit":
                op_label = get_text("dwlt_op_admin_credit", lang)
            elif op_type == "admin_debit":
                op_label = get_text("dwlt_op_admin_debit", lang)
            elif op_type == "recharge_debit":
                op_label = get_text("dwlt_op_recharge_debit", lang)
            else:
                op_label = get_text("dwlt_op_unknown", lang)

            amount: float = e["amount"]
            sign      = "+" if amount >= 0 else ""
            ref_type  = e.get("reference_type")
            ref_val   = e.get("reference_value")
            reference = f"{ref_type}: {ref_val}" if (ref_type and ref_val) else get_text("dwlt_no_reference", lang)
            notes     = e.get("notes") or get_text("dwlt_no_notes", lang)

            raw_source = e.get("created_source") or "telegram_admin"
            source_key = f"dwlt_source_{raw_source}"
            source = get_text(source_key, lang) if source_key in TEXTS else raw_source

            # Resolve created_by; SYSTEM_ACTOR (id=0) is falsy — shows "—"
            created_by_id = e.get("created_by")
            if created_by_id:
                admin_row = await db.get_user(created_by_id)
                if admin_row:
                    first = (admin_row.get("first_name") or "").strip()
                    last  = (admin_row.get("last_name")  or "").strip()
                    name  = f"{first} {last}".strip() or admin_row.get("username") or str(created_by_id)
                    created_by_label = f"{name} ({created_by_id})"
                else:
                    created_by_label = str(created_by_id)
            else:
                created_by_label = "—"

            text = get_text(
                "dwlt_entry_detail", lang,
                id=str(e["id"]),
                op_label=op_label,
                signed_amount=f"{sign}{format_amount_ledger(abs(amount))}",
                before=format_amount_ledger(e["balance_before"]),
                after=format_amount_ledger(e["balance_after"]),
                date=format_datetime(e["created_at"]),
                reference=reference,
                created_by=created_by_label,
                source=source,
                notes=notes,
            )
            await query.message.edit_text(
                text,
                reply_markup=dist_self_ledger_entry_keyboard(
                    callback_data.entry_id, callback_data.page, lang,
                ),
            )

    except Exception as exc:
        logger.error("Distributor self callback failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


# ---------------------------------------------------------------------------
# Admin: Preview Distributor UI — dev/testing only (no role/accounting change)
# ---------------------------------------------------------------------------

@router.callback_query(DistPreviewCallback.filter())
async def distributor_preview_callback(
    query: CallbackQuery,
    callback_data: DistPreviewCallback,
    db: Database,
    config: Config,
    distributor_service: DistributorService,
    distributor_wallet_service: DistributorWalletService,
) -> None:
    """Admin-only preview of the distributor self-service screens.
    Shows the exact same UI a real distributor would see for the chosen account.
    No role change, no accounting, no wallet writes."""
    user = query.from_user
    lang = await _lang(db, user.id)

    if not _is_admin(user.id, config):
        await query.answer(get_text("admin_not_authorized", lang), show_alert=True)
        return

    dist_id = callback_data.distributor_id
    dist = await distributor_service.get(dist_id)
    if not dist:
        await query.answer(get_text("dwlt_err_not_found", lang), show_alert=True)
        return

    action = callback_data.action

    try:
        if action == "wallet":
            balance = await distributor_wallet_service.get_balance(dist_id) or 0.0
            await query.message.edit_text(
                get_text("dist_self_wallet_title", lang, balance=format_amount(balance)),
                reply_markup=dist_preview_wallet_keyboard(dist_id, lang),
            )

        elif action in ("ledger", "lpage"):
            page      = callback_data.page
            page_size = config.DISTRIBUTOR_LEDGER_PAGE_SIZE
            total     = await distributor_wallet_service.count_ledger(dist_id)
            total_pages = max(1, (total + page_size - 1) // page_size)
            if total == 0:
                await query.message.edit_text(
                    get_text("dist_self_ledger_empty", lang),
                    reply_markup=dist_preview_wallet_keyboard(dist_id, lang),
                )
            else:
                entries = await distributor_wallet_service.get_ledger(
                    dist_id, limit=page_size, offset=page * page_size,
                )
                await query.message.edit_text(
                    get_text("dist_self_ledger_title", lang, count=str(total)),
                    reply_markup=dist_preview_ledger_keyboard(entries, dist_id, page, total_pages, lang),
                )

        elif action == "entry":
            e = await distributor_wallet_service.get_ledger_entry(callback_data.entry_id)
            if not e:
                await query.answer(get_text("dwlt_err_not_found", lang), show_alert=True)
                return

            op_type = e.get("operation_type", "")
            if op_type == "admin_credit":
                op_label = get_text("dwlt_op_admin_credit", lang)
            elif op_type == "admin_debit":
                op_label = get_text("dwlt_op_admin_debit", lang)
            elif op_type == "recharge_debit":
                op_label = get_text("dwlt_op_recharge_debit", lang)
            else:
                op_label = get_text("dwlt_op_unknown", lang)

            amount: float = e["amount"]
            sign      = "+" if amount >= 0 else ""
            ref_type  = e.get("reference_type")
            ref_val   = e.get("reference_value")
            reference = f"{ref_type}: {ref_val}" if (ref_type and ref_val) else get_text("dwlt_no_reference", lang)
            notes     = e.get("notes") or get_text("dwlt_no_notes", lang)

            raw_source = e.get("created_source") or "telegram_admin"
            source_key = f"dwlt_source_{raw_source}"
            source = get_text(source_key, lang) if source_key in TEXTS else raw_source

            created_by_id = e.get("created_by")
            if created_by_id:
                admin_row = await db.get_user(created_by_id)
                if admin_row:
                    first = (admin_row.get("first_name") or "").strip()
                    last  = (admin_row.get("last_name")  or "").strip()
                    name  = f"{first} {last}".strip() or admin_row.get("username") or str(created_by_id)
                    created_by_label = f"{name} ({created_by_id})"
                else:
                    created_by_label = str(created_by_id)
            else:
                created_by_label = "—"

            text = get_text(
                "dwlt_entry_detail", lang,
                id=str(e["id"]),
                op_label=op_label,
                signed_amount=f"{sign}{format_amount_ledger(abs(amount))}",
                before=format_amount_ledger(e["balance_before"]),
                after=format_amount_ledger(e["balance_after"]),
                date=format_datetime(e["created_at"]),
                reference=reference,
                created_by=created_by_label,
                source=source,
                notes=notes,
            )
            await query.message.edit_text(
                text,
                reply_markup=dist_preview_entry_keyboard(
                    dist_id, callback_data.entry_id, callback_data.page, lang,
                ),
            )

    except Exception as exc:
        logger.error("Distributor preview callback failed: %s", exc)
        await query.answer(get_text("error_generic", lang), show_alert=True)
        return

    await query.answer()


async def _show_dwlt_ledger(
    message: Message,
    lang: str,
    distributor_id: int,
    distributor_service: DistributorService,
    wallet_service: DistributorWalletService,
    config: Config,
    page: int = 0,
    edit: bool = False,
) -> None:
    """Render a paginated ledger list for one distributor."""
    d = await distributor_service.get(distributor_id)
    if not d:
        await message.answer(get_text("dwlt_err_not_found", lang))
        return

    page_size   = config.DISTRIBUTOR_LEDGER_PAGE_SIZE
    total       = await wallet_service.count_ledger(distributor_id)
    total_pages = max(1, (total + page_size - 1) // page_size)

    _send = message.edit_text if edit else message.answer

    if total == 0:
        await _send(
            get_text("dwlt_ledger_empty", lang),
            reply_markup=distributor_wallet_menu_keyboard(d, lang),
        )
        return

    entries = await wallet_service.get_ledger(
        distributor_id, limit=page_size, offset=page * page_size,
    )
    await _send(
        get_text("dwlt_ledger_title", lang,
                 name=d["full_name"], count=str(total)),
        reply_markup=distributor_ledger_keyboard(
            entries, distributor_id, page, total_pages, lang,
        ),
    )


async def _show_dwlt_entry(
    message: Message,
    lang: str,
    entry_id: int,
    page: int,
    wallet_service: DistributorWalletService,
    db: Database,
    edit: bool = False,
) -> None:
    """Render a single ledger entry detail screen."""
    e = await wallet_service.get_ledger_entry(entry_id)
    if not e:
        await message.answer(get_text("dwlt_err_not_found", lang))
        return

    op_type = e.get("operation_type", "")
    if op_type == "admin_credit":
        op_label = get_text("dwlt_op_admin_credit", lang)
    elif op_type == "admin_debit":
        op_label = get_text("dwlt_op_admin_debit", lang)
    elif op_type == "recharge_debit":
        op_label = get_text("dwlt_op_recharge_debit", lang)
    else:
        op_label = get_text("dwlt_op_unknown", lang)

    amount: float = e["amount"]
    sign          = "+" if amount >= 0 else ""
    ref_type  = e.get("reference_type")
    ref_val   = e.get("reference_value")
    reference = f"{ref_type}: {ref_val}" if (ref_type and ref_val) else get_text("dwlt_no_reference", lang)
    notes     = e.get("notes") or get_text("dwlt_no_notes", lang)

    # Translate created_source to the user's language
    raw_source = e.get("created_source") or "telegram_admin"
    source_key = f"dwlt_source_{raw_source}"
    source = get_text(source_key, lang) if source_key in TEXTS else raw_source

    # Resolve admin name from telegram_id for a human-readable "By" field
    created_by_id = e.get("created_by")
    if created_by_id:
        admin_row = await db.get_user(created_by_id)
        if admin_row:
            first = (admin_row.get("first_name") or "").strip()
            last  = (admin_row.get("last_name")  or "").strip()
            name  = f"{first} {last}".strip() or admin_row.get("username") or str(created_by_id)
            created_by_label = f"{name} ({created_by_id})"
        else:
            created_by_label = str(created_by_id)
    else:
        created_by_label = "—"

    text = get_text(
        "dwlt_entry_detail", lang,
        id=str(e["id"]),
        op_label=op_label,
        signed_amount=f"{sign}{format_amount_ledger(abs(amount))}",
        before=format_amount_ledger(e["balance_before"]),
        after=format_amount_ledger(e["balance_after"]),
        date=format_datetime(e["created_at"]),
        reference=reference,
        created_by=created_by_label,
        source=source,
        notes=notes,
    )
    _send = message.edit_text if edit else message.answer
    await _send(
        text,
        reply_markup=distributor_ledger_entry_keyboard(
            e["distributor_id"], entry_id, page, lang,
        ),
    )
