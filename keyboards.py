"""
keyboards.py — All inline keyboards built with aiogram 3.x CallbackData.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from typing import Any, Dict, List, Optional

from utils import get_text
from services import GAMES, GIFT_CARDS, DEPOSIT_AMOUNTS, OPERATOR_DISPLAY


# ---------------------------------------------------------------------------
# Callback data factories
# ---------------------------------------------------------------------------

class MenuCallback(CallbackData, prefix="menu"):
    action: str


class ConfirmCallback(CallbackData, prefix="rch"):
    phone: str
    amount: int
    rtype: str
    operator: Optional[str] = None  # set when the user manually picked the operator (auto-detect failed)
    token: Optional[str] = None  # single-use idempotency token, set on the confirm/cancel step


class CheckNowCallback(CallbackData, prefix="chknow"):
    """Purely presentational — asks the tracking card to re-render its
    current state on demand. Never triggers a new OneClick call or
    re-submission; it only re-reads the existing transaction row."""
    tx_id: int


class ActivyCallback(CallbackData, prefix="act"):
    phone: str
    operator: str    # internal operator key (mobilis/djezzy/ooredoo) — detected or user-chosen
    plan_code: str   # exact OneClick plan `code` from the live catalogue
    confirmed: int = 0  # 0=preview 1=confirm -1=cancel
    token: Optional[str] = None  # single-use idempotency token, set on the confirm/cancel step


class ActivyOperatorCallback(CallbackData, prefix="actop"):
    phone: str
    operator: str  # mobilis | djezzy | ooredoo — user-chosen when auto-detection fails


class ActivyNavCallback(CallbackData, prefix="actnav"):
    """Purely presentational navigation from the Activy offers grid — does
    not touch recharge logic, OneClick calls, or the database beyond the
    existing read-only lookups already used elsewhere."""
    phone: str
    action: str  # "back" (re-show operator choice) | "cancel"


class StdOperatorCallback(CallbackData, prefix="stdop"):
    phone: str
    amount: int
    operator: str  # mobilis | djezzy | ooredoo — user-chosen when auto-detection fails


class GameSelectCallback(CallbackData, prefix="gsel"):
    game_id: str


class GameConfirmCallback(CallbackData, prefix="gcfm"):
    game_id: str
    pkg_index: int
    confirmed: int = 0


class GiftSelectCallback(CallbackData, prefix="gfsel"):
    card_type: str


class GiftConfirmCallback(CallbackData, prefix="gfcfm"):
    card_type: str
    amount: int
    confirmed: int = 0


class LangCallback(CallbackData, prefix="lang"):
    code: str


class AdminCallback(CallbackData, prefix="adm"):
    action: str
    target_id: int = 0


class DistributorAdminCallback(CallbackData, prefix="dadm"):
    """Admin-only distributor management navigation (Phase 1: Foundation).
    Strictly scoped to profile/status/list/search — never touches wallet
    ledger, dashboard stats, or hierarchy (those are later phases)."""
    action: str   # "menu" | "create" | "list" | "search" | "detail" | "activate" | "suspend" | "page"
    distributor_id: int = 0
    page: int = 0


class DistributorWalletCallback(CallbackData, prefix="dwlt"):
    """Admin-only distributor wallet & ledger navigation (Phase 2).
    action values:
      menu        — show wallet balance + action buttons
      ask_credit  — trigger FSM: waiting_amount for a credit
      ask_debit   — trigger FSM: waiting_amount for a debit
      confirm     — execute the pending credit/debit (amount_cents + op carried)
      cancel      — abort the pending operation, return to wallet menu
      ledger      — paginated ledger list for this distributor
      lpage       — ledger page navigation
      entry       — show a single ledger entry's detail screen
    """
    action: str
    distributor_id: int = 0
    amount_cents: int = 0   # amount × 100 to avoid float precision in callback; only set on confirm
    op: str = "-"           # "credit" | "debit"; "-" = not applicable (avoids empty-string double-colon bug in aiogram pack/unpack)
    entry_id: int = 0       # set only on action="entry"
    page: int = 0           # current ledger page (for back navigation)


class DistributorConfirmCallback(CallbackData, prefix="drch"):
    """Confirm/cancel a distributor-funded standard recharge (Phase 3A).
    Mirrors ConfirmCallback (prefix 'rch') but routes exclusively to the
    distributor confirm handler, which calls DistributorRechargeService and
    debits the distributor wallet — the customer flow is left untouched."""
    phone: str
    amount: int
    rtype: str
    operator: Optional[str] = None  # set when the user manually picked the operator
    token: Optional[str] = None     # single-use idempotency token


class DistributorStdOperatorCallback(CallbackData, prefix="dstdop"):
    """Distributor-only operator-choice for standard recharge when
    auto-detection fails.  Carries phone/amount/operator into the distributor
    confirm screen without touching the customer StdOperatorCallback handler."""
    phone: str
    amount: int
    operator: str


class DistPreviewCallback(CallbackData, prefix="dprev"):
    """Admin-only: preview the distributor self-service UI for a specific distributor.
    Development/testing only — carries distributor_id explicitly (no role change)."""
    action: str        # "wallet" | "ledger" | "lpage" | "entry"
    distributor_id: int = 0
    entry_id: int = 0
    page: int = 0


class DistributorSelfCallback(CallbackData, prefix="dself"):
    """Distributor-facing self-service wallet & ledger navigation (Phase 3B).
    No distributor_id field — the handler resolves the account from user.id."""
    action: str   # "wallet" | "ledger" | "lpage" | "entry"
    entry_id: int = 0
    page: int = 0


class DistributorActivyCallback(CallbackData, prefix="dact"):
    """Distributor Activy offer selection/confirm/cancel (Phase 3C).
    Mirrors ActivyCallback (prefix 'act') exactly in structure but routes
    exclusively to distributor_activy_confirm_callback, which calls
    DistributorRechargeService — the customer ActivyCallback handler is
    never touched."""
    phone: str
    operator: str
    plan_code: str
    confirmed: int = 0      # 0=preview confirm screen  1=confirm  -1=cancel
    token: Optional[str] = None  # single-use idempotency token


class DistributorActivyOperatorCallback(CallbackData, prefix="dactop"):
    """Distributor operator-choice for Activy when auto-detection fails.
    Mirrors ActivyOperatorCallback (prefix 'actop') but routes to the
    distributor operator handler — customer flow is untouched."""
    phone: str
    operator: str  # mobilis | djezzy | ooredoo


class DistributorActivyNavCallback(CallbackData, prefix="dactnav"):
    """Back/Cancel navigation on the distributor Activy offers grid.
    Mirrors ActivyNavCallback (prefix 'actnav') — purely presentational,
    no wallet writes, no OneClick calls."""
    phone: str
    action: str  # "back" | "cancel"


class DepositCallback(CallbackData, prefix="dep"):
    action: str   # "menu" | "select" | "confirm" | "cancel"
    amount: int = 0


class AdminDepositCallback(CallbackData, prefix="adep"):
    action: str   # "list" | "approve" | "reject"
    request_id: int = 0


class FavoriteMenuCallback(CallbackData, prefix="favmenu"):
    """Top-level actions on the favorites list screen (not tied to one entry)."""
    action: str  # "add" | "search" | "list"


class FavoriteSelectCallback(CallbackData, prefix="favsel"):
    """Tapping a saved name opens its small action menu (Recharge/Activy/Rename/Delete)."""
    favorite_id: int


class FavoriteActionCallback(CallbackData, prefix="favact"):
    """Actions on one specific favorite. Recharge/Activy only ever hand the
    saved phone number to the existing, unmodified recharge/Activy entry
    points — no recharge/OneClick/wallet logic lives here."""
    favorite_id: int
    action: str  # "recharge" | "activy" | "rename" | "delete" | "delete_confirm" | "back"


class HistoryNavCallback(CallbackData, prefix="hnav"):
    """Purely presentational navigation on the history list screen —
    pagination, filter menu, filter selection, search prompt/clear."""
    action: str  # "page" | "filter_menu" | "filter_set" | "search" | "clear_search" | "clear_filter" | "back_to_list"
    page: int = 0
    value: Optional[str] = None  # date/status filter key, used only for action == "filter_set"


class HistorySelectCallback(CallbackData, prefix="hsel"):
    """Opening one transaction's details screen from the list."""
    tx_id: int
    page: int = 0  # remembered so "Back" returns to the same page


class HistoryActionCallback(CallbackData, prefix="hact"):
    """Actions on one specific transaction's details screen. Repeat/Add to
    Favorites only ever hand data to the existing, unmodified recharge/Activy/
    favorites entry points — no recharge/OneClick/wallet logic lives here."""
    tx_id: int
    page: int = 0
    action: str  # "repeat" | "add_favorite" | "copy_ref" | "back"


class DashboardCallback(CallbackData, prefix="dash"):
    """Admin Dashboard navigation — strictly read-only, never touches
    recharge/wallet/OneClick/transaction business logic."""
    screen: str        # "home" | "stats" | "diagnostics" | "alerts"
    action: Optional[str] = None  # e.g. "refresh"; screen-specific value below
    value: Optional[str] = None   # e.g. statistics period key ("today", "7days", "custom")


class OpsCallback(CallbackData, prefix="ops"):
    """Operations Center navigation — strictly read-only, never touches
    recharge/wallet/OneClick/transaction business logic. Row taps into a
    single transaction reuse the existing, unmodified history details
    rendering (see ops_tx_detail_keyboard / _history_details_text)."""
    screen: str        # "home" | "processing" | "completed" | "tx_detail"
    action: Optional[str] = None  # e.g. "refresh"
    value: Optional[str] = None   # e.g. tx_id for "tx_detail"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _btn(text: str, callback: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback.pack())


# ---------------------------------------------------------------------------
# Wallet / balance view
# ---------------------------------------------------------------------------

def wallet_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Customer wallet screen."""
    b = InlineKeyboardBuilder()

    b.row(
        _btn(
            get_text("btn_deposit", lang),
            DepositCallback(action="menu"),
        )
    )

    b.row(
        _btn(
            "⬅️ العودة للقائمة الرئيسية",
            DepositCallback(action="exit"),
        )
    )

    return b.as_markup()


# ---------------------------------------------------------------------------
# Deposit flow
# ---------------------------------------------------------------------------

def deposit_amounts_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for amount in DEPOSIT_AMOUNTS:
        b.row(InlineKeyboardButton(
            text=f"{amount:,} دج",
            callback_data=DepositCallback(action="select", amount=amount).pack(),
        ))
    b.row(_btn(get_text("btn_back", lang), DepositCallback(action="cancel")))
    return b.as_markup()


def deposit_confirm_keyboard(amount: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang), DepositCallback(action="confirm", amount=amount)),
        _btn(get_text("btn_cancel",  lang), DepositCallback(action="cancel")),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Admin deposit approval
# ---------------------------------------------------------------------------

def admin_deposit_action_keyboard(request_id: int, lang: str) -> InlineKeyboardMarkup:
    approve_label = {"ar": "✅ موافقة", "en": "✅ Approve"}.get(lang, "✅ Approve")
    reject_label  = {"ar": "❌ رفض",   "en": "❌ Reject"}.get(lang,  "❌ Reject")
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=approve_label,
            callback_data=AdminDepositCallback(action="approve", request_id=request_id).pack(),
        ),
        InlineKeyboardButton(
            text=reject_label,
            callback_data=AdminDepositCallback(action="reject", request_id=request_id).pack(),
        ),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Recharge confirm / cancel
# ---------------------------------------------------------------------------

def confirm_recharge_keyboard(
    phone: str, amount: int, lang: str, operator: Optional[str] = None, token: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    token: single-use idempotency token minted for this confirmation screen
    (mirrors the Activy confirm pattern). It is echoed back on the Confirm
    tap so the handler can detect and safely ignore a duplicate confirm
    (retry or double-tap).
    """
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang),
             ConfirmCallback(phone=phone, amount=amount, rtype="standard", operator=operator, token=token)),
        _btn(get_text("btn_cancel",  lang), MenuCallback(action="cancel")),
    )
    return b.as_markup()


def recharge_failure_keyboard(
    phone: str, amount: int, lang: str, operator: Optional[str] = None, token: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Shown on a failed standard recharge. Retry re-sends the exact same
    ConfirmCallback used by the normal confirm screen (same phone/amount/
    operator) — no new retry logic, just re-entering the existing flow.
    A fresh token is minted by the caller for this Retry button, exactly
    like Activy's failure keyboard.
    """
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_retry", lang),
             ConfirmCallback(phone=phone, amount=amount, rtype="standard", operator=operator, token=token)),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Distributor recharge confirm / failure / operator-choice (Phase 3A)
# ---------------------------------------------------------------------------

def distributor_confirm_recharge_keyboard(
    phone: str, amount: int, lang: str, operator: Optional[str] = None, token: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Confirm/Cancel for a distributor-funded standard recharge.
    Uses DistributorConfirmCallback (prefix 'drch') so aiogram routes it
    exclusively to the distributor confirm handler — the customer
    ConfirmCallback handler (prefix 'rch') is never touched."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang),
             DistributorConfirmCallback(phone=phone, amount=amount, rtype="standard",
                                        operator=operator, token=token)),
        _btn(get_text("btn_cancel", lang), MenuCallback(action="cancel")),
    )
    return b.as_markup()


def distributor_recharge_failure_keyboard(
    phone: str, amount: int, lang: str, operator: Optional[str] = None, token: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Retry button on a failed distributor recharge. The caller may pass a
    fresh token so the Retry confirm tap is also idempotency-protected."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_retry", lang),
             DistributorConfirmCallback(phone=phone, amount=amount, rtype="standard",
                                        operator=operator, token=token)),
    )
    return b.as_markup()


def distributor_std_operator_choice_keyboard(phone: str, amount: int, lang: str) -> InlineKeyboardMarkup:
    """Operator-choice screen shown to a distributor when phone auto-detection
    fails.  Uses DistributorStdOperatorCallback so the customer
    StdOperatorCallback handler is never involved."""
    b = InlineKeyboardBuilder()
    for operator in ("mobilis", "djezzy", "ooredoo"):
        label = OPERATOR_DISPLAY.get(operator, {}).get(lang, operator.capitalize())
        emoji = OPERATOR_DISPLAY.get(operator, {}).get("emoji", "")
        b.row(InlineKeyboardButton(
            text=f"{emoji} {label}".strip(),
            callback_data=DistributorStdOperatorCallback(phone=phone, amount=amount, operator=operator).pack(),
        ))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Phase 3C — Distributor Activy keyboards
# All functions below mirror their customer-flow equivalents in structure but
# emit DistributorActivy* callbacks exclusively so aiogram routes them to the
# dedicated distributor handlers.  The customer Activy keyboards and handlers
# are completely untouched.
# ---------------------------------------------------------------------------

def distributor_activy_operator_choice_keyboard(phone: str, lang: str) -> InlineKeyboardMarkup:
    """Operator-choice shown to a distributor when Activy phone auto-detection
    fails.  Uses DistributorActivyOperatorCallback (prefix 'dactop') so the
    customer ActivyOperatorCallback handler is never involved."""
    b = InlineKeyboardBuilder()
    for operator in ("mobilis", "djezzy", "ooredoo"):
        label = OPERATOR_DISPLAY.get(operator, {}).get(lang, operator.capitalize())
        emoji = OPERATOR_DISPLAY.get(operator, {}).get("emoji", "")
        b.row(InlineKeyboardButton(
            text=f"{emoji} {label}".strip(),
            callback_data=DistributorActivyOperatorCallback(phone=phone, operator=operator).pack(),
        ))
    return b.as_markup()


def distributor_activy_offers_keyboard(
    phone: str, operator: str, plans: List[Dict[str, Any]], lang: str
) -> InlineKeyboardMarkup:
    """2-column offers grid for a distributor Activy session.
    Tapping an offer emits DistributorActivyCallback(confirmed=0) — the
    confirm screen is shown by the handler before any money moves.
    Back/Cancel emit DistributorActivyNavCallback (prefix 'dactnav') so
    the customer ActivyNavCallback handler is never involved."""
    b = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=f"{plan.get('name', plan.get('code', ''))} — {plan.get('amount')} دج",
            callback_data=DistributorActivyCallback(
                phone=phone, operator=operator, plan_code=plan["code"], confirmed=0
            ).pack(),
        )
        for plan in plans
    ]
    for i in range(0, len(buttons), 2):
        b.row(*buttons[i:i + 2])
    b.row(
        _btn(get_text("btn_back", lang),   DistributorActivyNavCallback(phone=phone, action="back")),
        _btn(get_text("btn_cancel", lang), DistributorActivyNavCallback(phone=phone, action="cancel")),
    )
    return b.as_markup()


def distributor_activy_confirm_keyboard(
    phone: str, operator: str, plan_code: str, token: str, lang: str
) -> InlineKeyboardMarkup:
    """Confirm/Cancel for a distributor Activy activation.
    token is a single-use idempotency token minted by the handler.
    Uses DistributorActivyCallback (prefix 'dact') so the customer
    ActivyCallback handler is never involved."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang),
             DistributorActivyCallback(phone=phone, operator=operator, plan_code=plan_code,
                                       confirmed=1, token=token)),
        _btn(get_text("btn_cancel", lang),
             DistributorActivyCallback(phone=phone, operator=operator, plan_code=plan_code,
                                       confirmed=-1, token=token)),
    )
    return b.as_markup()


def distributor_activy_failure_keyboard(
    phone: str, operator: str, plan_code: str, lang: str
) -> InlineKeyboardMarkup:
    """Retry button on a failed distributor Activy activation.
    Retry sends confirmed=0 so the confirm screen is re-shown with a fresh
    idempotency token — exactly as if the user had tapped the offer again."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_retry", lang),
             DistributorActivyCallback(phone=phone, operator=operator,
                                       plan_code=plan_code, confirmed=0)),
    )
    return b.as_markup()


def tracking_keyboard(tx_id: int, lang: str) -> InlineKeyboardMarkup:
    """Shown on the in-flight tracking card while status is still
    pending/processing. Removed once the card is edited to its final
    success/failure state."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("btn_check_now", lang), CheckNowCallback(tx_id=tx_id)))
    return b.as_markup()


def std_operator_choice_keyboard(phone: str, amount: int, lang: str) -> InlineKeyboardMarkup:
    """
    Shown for a standard (phone*amount) recharge when the operator can't be
    auto-detected from the phone number. Lets the user pick the operator
    manually so the correct PREPAID_<OPERATOR> plan_code can be used.
    """
    b = InlineKeyboardBuilder()
    for operator in ("mobilis", "djezzy", "ooredoo"):
        label = OPERATOR_DISPLAY.get(operator, {}).get(lang, operator.capitalize())
        emoji = OPERATOR_DISPLAY.get(operator, {}).get("emoji", "")
        b.row(InlineKeyboardButton(
            text=f"{emoji} {label}".strip(),
            callback_data=StdOperatorCallback(phone=phone, amount=amount, operator=operator).pack(),
        ))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Activy
# ---------------------------------------------------------------------------

def activy_offers_keyboard(
    phone: str, operator: str, plans: List[Dict[str, Any]], lang: str
) -> InlineKeyboardMarkup:
    """
    plans: live fixedPlans (as returned by OneClickAPI.get_fixed_plans),
    each expected to have "code", "name", and "amount" keys.
    operator: internal operator key (mobilis/djezzy/ooredoo) — either
    auto-detected from the phone or chosen manually by the user; carried
    in the callback so downstream steps don't need to re-detect it.

    Rendered as a 2-column grid (two offers per row) for a more compact,
    scannable layout — purely visual, the underlying plan list/order is
    untouched.
    """
    b = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=f"{plan.get('name', plan.get('code', ''))} — {plan.get('amount')} دج",
            callback_data=ActivyCallback(
                phone=phone, operator=operator, plan_code=plan["code"], confirmed=0
            ).pack(),
        )
        for plan in plans
    ]
    for i in range(0, len(buttons), 2):
        b.row(*buttons[i:i + 2])
    b.row(
        _btn(get_text("btn_back", lang), ActivyNavCallback(phone=phone, action="back")),
        _btn(get_text("btn_cancel", lang), ActivyNavCallback(phone=phone, action="cancel")),
    )
    return b.as_markup()


def activy_confirm_keyboard(
    phone: str, operator: str, plan_code: str, token: str, lang: str
) -> InlineKeyboardMarkup:
    """
    token: single-use idempotency token minted for this confirmation screen.
    It is echoed back on the Confirm/Cancel taps so the handler can detect
    and safely ignore a duplicate confirm (retry or double-tap).
    """
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang),
             ActivyCallback(phone=phone, operator=operator, plan_code=plan_code, confirmed=1, token=token)),
        _btn(get_text("btn_cancel",  lang),
             ActivyCallback(phone=phone, operator=operator, plan_code=plan_code, confirmed=-1, token=token)),
    )
    return b.as_markup()


def activy_failure_keyboard(
    phone: str, operator: str, plan_code: str, lang: str
) -> InlineKeyboardMarkup:
    """
    Shown on a failed Activy activation. Retry sends the user back to the
    same offer's confirm screen (confirmed=0) so a fresh idempotency token
    gets minted there, exactly like reopening the offer normally.
    """
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_retry", lang),
             ActivyCallback(phone=phone, operator=operator, plan_code=plan_code, confirmed=0)),
    )
    return b.as_markup()


def activy_operator_choice_keyboard(phone: str, lang: str) -> InlineKeyboardMarkup:
    """
    Shown when the operator can't be auto-detected from the phone number.
    Lets the user pick the operator manually so live plans can still be
    fetched from the OneClick catalogue.
    """
    b = InlineKeyboardBuilder()
    for operator in ("mobilis", "djezzy", "ooredoo"):
        label = OPERATOR_DISPLAY.get(operator, {}).get(lang, operator.capitalize())
        emoji = OPERATOR_DISPLAY.get(operator, {}).get("emoji", "")
        b.row(InlineKeyboardButton(
            text=f"{emoji} {label}".strip(),
            callback_data=ActivyOperatorCallback(phone=phone, operator=operator).pack(),
        ))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Favorite Numbers (address book)
# ---------------------------------------------------------------------------

def favorites_list_keyboard(favorites: List[Dict[str, Any]], lang: str) -> InlineKeyboardMarkup:
    """
    2-column grid of saved names (matching the Activy offers grid style),
    followed by Search and Add New action rows. Selecting a name opens its
    small action menu (favorite_actions_keyboard) — it never starts a
    recharge/Activy flow directly from this screen.
    """
    b = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=f"👤 {fav['label']}",
            callback_data=FavoriteSelectCallback(favorite_id=fav["id"]).pack(),
        )
        for fav in favorites
    ]
    for i in range(0, len(buttons), 2):
        b.row(*buttons[i:i + 2])
    if len(favorites) > 5:
        b.row(_btn(get_text("btn_favorites_search", lang), FavoriteMenuCallback(action="search")))
    b.row(_btn(get_text("btn_favorites_add", lang), FavoriteMenuCallback(action="add")))
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="home")))
    return b.as_markup()


def favorite_actions_keyboard(favorite_id: int, lang: str) -> InlineKeyboardMarkup:
    """Small action menu shown after tapping a saved name."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_favorites_recharge", lang), FavoriteActionCallback(favorite_id=favorite_id, action="recharge")),
        _btn(get_text("btn_favorites_activy", lang),    FavoriteActionCallback(favorite_id=favorite_id, action="activy")),
    )
    b.row(
        _btn(get_text("btn_favorites_rename", lang), FavoriteActionCallback(favorite_id=favorite_id, action="rename")),
        _btn(get_text("btn_favorites_delete", lang), FavoriteActionCallback(favorite_id=favorite_id, action="delete")),
    )
    b.row(_btn(get_text("btn_back", lang), FavoriteMenuCallback(action="list")))
    return b.as_markup()


def favorite_delete_confirm_keyboard(favorite_id: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang), FavoriteActionCallback(favorite_id=favorite_id, action="delete_confirm")),
        _btn(get_text("btn_cancel",  lang), FavoriteActionCallback(favorite_id=favorite_id, action="back")),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Professional Transaction History (read-only, except Repeat/Add to Favorites)
# ---------------------------------------------------------------------------

DATE_FILTERS = ("today", "yesterday", "7days")
STATUS_FILTERS = ("success", "failed")


def history_list_keyboard(
    transactions: List[Dict[str, Any]],
    page: int,
    total_pages: int,
    has_search: bool,
    has_filter: bool,
    lang: str,
) -> InlineKeyboardMarkup:
    """One row per transaction (tap to view details), then pagination,
    filter/search controls, and a Back-to-menu row."""
    b = InlineKeyboardBuilder()
    for tx in transactions:
        b.row(InlineKeyboardButton(
            text=_history_row_label(tx, lang),
            callback_data=HistorySelectCallback(tx_id=tx["id"], page=page).pack(),
        ))

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(_btn("⬅️", HistoryNavCallback(action="page", page=page - 1)))
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}", callback_data=HistoryNavCallback(action="page", page=page).pack(),
        ))
        if page < total_pages - 1:
            nav_row.append(_btn("➡️", HistoryNavCallback(action="page", page=page + 1)))
        b.row(*nav_row)

    filter_row = [_btn(get_text("btn_history_filter", lang), HistoryNavCallback(action="filter_menu"))]
    if has_filter:
        filter_row.append(_btn(get_text("btn_history_clear_filter", lang), HistoryNavCallback(action="clear_filter")))
    b.row(*filter_row)

    search_row = [_btn(get_text("btn_history_search", lang), HistoryNavCallback(action="search"))]
    if has_search:
        search_row.append(_btn(get_text("btn_history_clear_search", lang), HistoryNavCallback(action="clear_search")))
    b.row(*search_row)

    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="home")))
    return b.as_markup()


def _history_row_label(tx: Dict[str, Any], lang: str) -> str:
    icon = {"success": "✅", "failed": "❌"}.get(tx["status"], "⏳")
    op_key = (tx.get("operator") or "").lower()
    if not op_key and tx.get("phone"):
        from services import OperatorDetector
        op_key = OperatorDetector.detect(tx["phone"])
    operator = OPERATOR_DISPLAY.get(op_key, tx.get("operator") or "—")
    type_label = get_text(f"history_type_{tx['type']}", lang)
    amount = f"{tx['amount']:,.0f} دج"
    time_part = (tx["created_at"] or "")[5:16].replace("T", " ")  # MM-DD HH:MM
    name_part = f" ({tx['favorite_label']})" if tx.get("favorite_label") else ""
    return f"{icon} {tx['phone']}{name_part} • {operator} • {type_label} • {amount} • {time_part}"


def history_filter_keyboard(current_date: Optional[str], current_status: Optional[str], lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn(("✅ " if current_date == "today" else "") + get_text("filter_today", lang), HistoryNavCallback(action="filter_set", value="today")),
        _btn(("✅ " if current_date == "yesterday" else "") + get_text("filter_yesterday", lang), HistoryNavCallback(action="filter_set", value="yesterday")),
    )
    b.row(_btn(("✅ " if current_date == "7days" else "") + get_text("filter_7days", lang), HistoryNavCallback(action="filter_set", value="7days")))
    b.row(
        _btn(("✅ " if current_status == "success" else "") + get_text("filter_success", lang), HistoryNavCallback(action="filter_set", value="success")),
        _btn(("✅ " if current_status == "failed" else "") + get_text("filter_failed", lang), HistoryNavCallback(action="filter_set", value="failed")),
    )
    b.row(_btn(get_text("btn_back", lang), HistoryNavCallback(action="back_to_list")))
    return b.as_markup()


def history_details_keyboard(tx: Dict[str, Any], page: int, is_favorited: bool, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("btn_history_repeat", lang), HistoryActionCallback(tx_id=tx["id"], page=page, action="repeat")))
    row2 = []
    if not is_favorited:
        row2.append(_btn(get_text("btn_history_add_favorite", lang), HistoryActionCallback(tx_id=tx["id"], page=page, action="add_favorite")))
    if tx.get("reference"):
        row2.append(_btn(get_text("btn_history_copy_ref", lang), HistoryActionCallback(tx_id=tx["id"], page=page, action="copy_ref")))
    if row2:
        b.row(*row2)
    b.row(_btn(get_text("btn_back", lang), HistoryActionCallback(tx_id=tx["id"], page=page, action="back")))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------

def games_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for game_id, game in GAMES.items():
        name = game["name_ar"] if lang == "ar" else game["name_en"]
        b.row(InlineKeyboardButton(
            text=f"{game['emoji']} {name}",
            callback_data=GameSelectCallback(game_id=game_id).pack(),
        ))
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="home")))
    return b.as_markup()


def game_packages_keyboard(game_id: str, lang: str) -> InlineKeyboardMarkup:
    game = GAMES[game_id]
    b = InlineKeyboardBuilder()
    for i, pkg in enumerate(game["packages"]):
        b.row(InlineKeyboardButton(
            text=f"{pkg['amount']} {game['currency']} — {pkg['price']} دج",
            callback_data=GameConfirmCallback(game_id=game_id, pkg_index=i, confirmed=0).pack(),
        ))
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="games")))
    return b.as_markup()


def game_confirm_keyboard(game_id: str, pkg_index: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang), GameConfirmCallback(game_id=game_id, pkg_index=pkg_index, confirmed=1)),
        _btn(get_text("btn_cancel",  lang), GameConfirmCallback(game_id=game_id, pkg_index=pkg_index, confirmed=-1)),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Gift cards
# ---------------------------------------------------------------------------

def gift_cards_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for card_type, card in GIFT_CARDS.items():
        name = card["name_ar"] if lang == "ar" else card["name_en"]
        b.row(InlineKeyboardButton(
            text=f"{card['emoji']} {name}",
            callback_data=GiftSelectCallback(card_type=card_type).pack(),
        ))
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="home")))
    return b.as_markup()


def gift_amounts_keyboard(card_type: str, lang: str) -> InlineKeyboardMarkup:
    card = GIFT_CARDS[card_type]
    b = InlineKeyboardBuilder()
    for amount in card["amounts"]:
        b.row(InlineKeyboardButton(
            text=f"{amount} دج",
            callback_data=GiftConfirmCallback(card_type=card_type, amount=amount, confirmed=0).pack(),
        ))
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="gift_cards")))
    return b.as_markup()


def gift_confirm_keyboard(card_type: str, amount: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn(get_text("btn_confirm", lang), GiftConfirmCallback(card_type=card_type, amount=amount, confirmed=1)),
        _btn(get_text("btn_cancel",  lang), GiftConfirmCallback(card_type=card_type, amount=amount, confirmed=-1)),
    )
    return b.as_markup()



def help_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("btn_back", lang), MenuCallback(action="home")))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

def language_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🇩🇿 العربية", callback_data=LangCallback(code="ar").pack()),
        InlineKeyboardButton(text="🇬🇧 English",  callback_data=LangCallback(code="en").pack()),
    )
    return b.as_markup()


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

def admin_keyboard(lang: str, mock_mode: bool) -> InlineKeyboardMarkup:
    """Compact and organized Admin panel keyboard."""
    mock_status = get_text("mock_on", lang) if mock_mode else get_text("mock_off", lang)

    b = InlineKeyboardBuilder()

    # 📊 Monitoring
    b.row(
        InlineKeyboardButton(
            text=get_text("admin_btn_stats", lang),
            callback_data=AdminCallback(action="stats").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_dashboard", lang),
            callback_data=DashboardCallback(screen="home").pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=get_text("admin_btn_logs", lang),
            callback_data=AdminCallback(action="logs").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_txns", lang),
            callback_data=AdminCallback(action="txns").pack(),
        ),
    )

    # 👥 Users & money
    b.row(
        InlineKeyboardButton(
            text=get_text("admin_btn_users", lang),
            callback_data=AdminCallback(action="users").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_add_balance", lang),
            callback_data=AdminCallback(action="add_balance").pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=get_text("admin_btn_deposits", lang),
            callback_data=AdminDepositCallback(action="list").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_distributors", lang),
            callback_data=DistributorAdminCallback(action="menu").pack(),
        ),
    )

    # 🛠 Operations
    b.row(
        InlineKeyboardButton(
            text=get_text("admin_btn_ops_center", lang),
            callback_data=OpsCallback(screen="home").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_broadcast", lang),
            callback_data=AdminCallback(action="broadcast").pack(),
        ),
    )

    # 🔧 Developer / testing tools
    b.row(
        InlineKeyboardButton(
            text=f"🔧 Mock: {mock_status}",
            callback_data=AdminCallback(action="toggle_mock").pack(),
        ),
        InlineKeyboardButton(
            text=get_text("admin_btn_preview_dist", lang),
            callback_data=AdminCallback(action="preview_dist").pack(),
        ),
    )

    # 🏠 Exit
    b.row(
        InlineKeyboardButton(
            text="⬅️ العودة للقائمة الرئيسية",
            callback_data=AdminCallback(action="exit").pack(),
        )
    )

    return b.as_markup()


# ---------------------------------------------------------------------------
# Distributor Management — Admin screens (Phase 1: Foundation)
# ---------------------------------------------------------------------------

def distributor_admin_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("dadm_btn_create", lang), DistributorAdminCallback(action="create")))
    b.row(_btn(get_text("dadm_btn_list", lang),   DistributorAdminCallback(action="list")))
    b.row(_btn(get_text("dadm_btn_search", lang), DistributorAdminCallback(action="search")))
    b.row(_btn(get_text("btn_back", lang),  AdminCallback(action="panel")))
    return b.as_markup()


def _distributor_row_label(d: Dict[str, Any]) -> str:
    icon = "🟢" if d["status"] == "active" else "🔴"
    return f"{icon} {d['full_name']} · {d['telegram_id']}"


def distributor_list_keyboard(
    distributors: List[Dict[str, Any]], page: int, total_pages: int, lang: str, search: bool = False,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for d in distributors:
        b.row(InlineKeyboardButton(
            text=_distributor_row_label(d),
            callback_data=DistributorAdminCallback(action="detail", distributor_id=d["id"], page=page).pack(),
        ))

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(_btn("⬅️", DistributorAdminCallback(action="page", page=page - 1)))
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=DistributorAdminCallback(action="page", page=page).pack(),
        ))
        if page < total_pages - 1:
            nav_row.append(_btn("➡️", DistributorAdminCallback(action="page", page=page + 1)))
        b.row(*nav_row)

    b.row(_btn(get_text("btn_back", lang), DistributorAdminCallback(action="menu")))
    return b.as_markup()


def distributor_detail_keyboard(distributor: Dict[str, Any], page: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if distributor["status"] == "active":
        b.row(_btn(get_text("dadm_btn_suspend", lang),
                    DistributorAdminCallback(action="suspend", distributor_id=distributor["id"], page=page)))
    else:
        b.row(_btn(get_text("dadm_btn_activate", lang),
                    DistributorAdminCallback(action="activate", distributor_id=distributor["id"], page=page)))
    b.row(_btn(get_text("dadm_btn_wallet", lang),
               DistributorWalletCallback(action="menu", distributor_id=distributor["id"])))
    b.row(_btn(get_text("btn_back", lang), DistributorAdminCallback(action="list", page=page)))
    return b.as_markup()


def distributor_wallet_menu_keyboard(distributor: Dict[str, Any], lang: str) -> InlineKeyboardMarkup:
    """Wallet action menu shown after tapping 💳 Wallet in the distributor detail screen."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("dwlt_btn_credit", lang), DistributorWalletCallback(action="ask_credit", distributor_id=distributor["id"])))
    b.row(_btn(get_text("dwlt_btn_debit",  lang), DistributorWalletCallback(action="ask_debit",  distributor_id=distributor["id"])))
    b.row(_btn(get_text("dwlt_btn_ledger", lang), DistributorWalletCallback(action="ledger",     distributor_id=distributor["id"])))
    b.row(_btn(get_text("btn_back", lang),        DistributorAdminCallback(action="detail",       distributor_id=distributor["id"])))
    return b.as_markup()


def distributor_wallet_confirm_keyboard(
    distributor_id: int, amount_cents: int, op: str, lang: str,
) -> InlineKeyboardMarkup:
    """Confirm/Cancel row for a pending credit or debit."""
    b = InlineKeyboardBuilder()
    b.row(
        _btn(
            get_text("btn_confirm", lang),
            DistributorWalletCallback(action="confirm", distributor_id=distributor_id,
                                      amount_cents=amount_cents, op=op),
        ),
        _btn(
            get_text("btn_cancel", lang),
            DistributorWalletCallback(action="cancel", distributor_id=distributor_id, op=op),
        ),
    )
    return b.as_markup()


def distributor_ledger_keyboard(
    entries: List[Dict[str, Any]], distributor_id: int,
    page: int, total_pages: int, lang: str,
) -> InlineKeyboardMarkup:
    """Paginated ledger list; each row opens the entry detail screen."""
    b = InlineKeyboardBuilder()
    for e in entries:
        sign = "➕" if e["amount"] > 0 else "➖"
        abs_amount = abs(e["amount"])
        op_type = e.get("operation_type", "")
        if op_type == "admin_credit":
            op_label = get_text("dwlt_op_admin_credit", lang)
        elif op_type == "recharge_debit":
            op_label = get_text("dwlt_op_recharge_debit", lang)
        else:
            op_label = get_text("dwlt_op_admin_debit", lang)
        date_short = e["created_at"][:10] if e.get("created_at") else "—"
        row_label = f"{sign} {abs_amount:,.0f}  {op_label}  {date_short}"
        b.row(InlineKeyboardButton(
            text=row_label,
            callback_data=DistributorWalletCallback(
                action="entry", distributor_id=distributor_id, entry_id=e["id"], page=page,
            ).pack(),
        ))

    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=DistributorWalletCallback(action="lpage", distributor_id=distributor_id, page=page - 1).pack(),
            ))
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=DistributorWalletCallback(action="lpage", distributor_id=distributor_id, page=page).pack(),
        ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=DistributorWalletCallback(action="lpage", distributor_id=distributor_id, page=page + 1).pack(),
            ))
        b.row(*nav_row)

    b.row(_btn(get_text("btn_back", lang), DistributorWalletCallback(action="menu", distributor_id=distributor_id)))
    return b.as_markup()


def distributor_ledger_entry_keyboard(
    distributor_id: int, entry_id: int, page: int, lang: str,
) -> InlineKeyboardMarkup:
    """Single back-button shown on a ledger entry detail screen."""
    b = InlineKeyboardBuilder()
    b.row(_btn(
        get_text("btn_back", lang),
        DistributorWalletCallback(action="ledger", distributor_id=distributor_id, page=page),
    ))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Admin Dashboard (read-only reporting/diagnostics — see dashboard.py)
# ---------------------------------------------------------------------------

def dashboard_home_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_stats", lang),       callback_data=DashboardCallback(screen="stats", value="today").pack()),
        InlineKeyboardButton(text=get_text("dash_btn_diagnostics", lang), callback_data=DashboardCallback(screen="diagnostics").pack()),
    )
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_alerts", lang),      callback_data=DashboardCallback(screen="alerts").pack()),
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang),     callback_data=DashboardCallback(screen="home", action="refresh").pack()),
    )
    b.row(_btn(get_text("btn_back", lang), AdminCallback(action="panel")))
    return b.as_markup()


def dashboard_stats_keyboard(lang: str, period: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    periods = [
        ("today", get_text("dash_period_today", lang)),
        ("yesterday", get_text("dash_period_yesterday", lang)),
        ("7days", get_text("dash_period_7days", lang)),
        ("30days", get_text("dash_period_30days", lang)),
    ]
    row = []
    for key, label in periods:
        text = f"• {label}" if key == period else label
        row.append(InlineKeyboardButton(text=text, callback_data=DashboardCallback(screen="stats", value=key).pack()))
        if len(row) == 2:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang), callback_data=DashboardCallback(screen="stats", action="refresh", value=period).pack()),
    )
    b.row(_btn(get_text("btn_back", lang), DashboardCallback(screen="home")))
    return b.as_markup()


def dashboard_diagnostics_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang), callback_data=DashboardCallback(screen="diagnostics", action="refresh").pack()),
    )
    b.row(_btn(get_text("btn_back", lang), DashboardCallback(screen="home")))
    return b.as_markup()


def dashboard_alerts_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang), callback_data=DashboardCallback(screen="alerts", action="refresh").pack()),
    )
    b.row(_btn(get_text("btn_back", lang), DashboardCallback(screen="home")))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Operations Center (read-only live ops view — see ops_center.py)
# ---------------------------------------------------------------------------

def ops_center_home_keyboard(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=get_text("ops_btn_processing", lang), callback_data=OpsCallback(screen="processing").pack()),
        InlineKeyboardButton(text=get_text("ops_btn_completed", lang),  callback_data=OpsCallback(screen="completed").pack()),
    )
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_diagnostics", lang), callback_data=DashboardCallback(screen="diagnostics").pack()),
        InlineKeyboardButton(text=get_text("dash_btn_alerts", lang),      callback_data=DashboardCallback(screen="alerts").pack()),
    )
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang), callback_data=OpsCallback(screen="home", action="refresh").pack()),
    )
    b.row(_btn(get_text("btn_back", lang), AdminCallback(action="panel")))
    return b.as_markup()


def _ops_row_label(tx: Dict[str, Any], lang: str) -> str:
    icon = {"success": "✅", "failed": "❌"}.get(tx["status"], "⏳")
    op_key = (tx.get("operator") or "").lower()
    if not op_key and tx.get("phone"):
        from services import OperatorDetector
        op_key = OperatorDetector.detect(tx["phone"])
    operator = OPERATOR_DISPLAY.get(op_key, tx.get("operator") or "—")
    amount = f"{tx['amount']:,.0f} دج"
    time_part = (tx["created_at"] or "")[5:16].replace("T", " ")
    name_part = f" ({tx['favorite_label']})" if tx.get("favorite_label") else ""
    return f"{icon} {tx['phone']}{name_part} • {operator} • {amount} • {time_part}"


def ops_list_keyboard(transactions: List[Dict[str, Any]], screen: str, lang: str) -> InlineKeyboardMarkup:
    """Shared list keyboard for both the Processing and Last-Completed
    screens. Row taps open the existing history details rendering via
    OpsCallback(screen='tx_detail')."""
    b = InlineKeyboardBuilder()
    for tx in transactions:
        b.row(InlineKeyboardButton(
            text=_ops_row_label(tx, lang),
            callback_data=OpsCallback(screen="tx_detail", value=str(tx["id"])).pack(),
        ))
    b.row(
        InlineKeyboardButton(text=get_text("dash_btn_refresh", lang), callback_data=OpsCallback(screen=screen, action="refresh").pack()),
    )
    b.row(_btn(get_text("btn_back", lang), OpsCallback(screen="home")))
    return b.as_markup()


def ops_tx_detail_keyboard(tx: Dict[str, Any], lang: str) -> InlineKeyboardMarkup:
    """Minimal, read-only detail keyboard for a transaction viewed from the
    Operations Center. Reuses the same details TEXT rendering as Transaction
    History (_history_details_text) but intentionally omits Repeat/Add to
    Favorites — those are user-scoped actions that don't make sense when an
    admin is looking at an arbitrary user's transaction."""
    b = InlineKeyboardBuilder()
    if tx.get("reference"):
        b.row(_btn(get_text("btn_history_copy_ref", lang), OpsCallback(screen="tx_detail", action="copy_ref", value=str(tx["id"]))))
    b.row(_btn(get_text("btn_back", lang), OpsCallback(screen="home")))
    return b.as_markup()


def admin_user_actions_keyboard(
    target_telegram_id: int,
    lang: str,
    is_banned: bool = False,
) -> InlineKeyboardMarkup:
    add_label = {"ar": "➕ إضافة رصيد", "en": "➕ Add Balance"}.get(lang, "➕ Add Balance")
    sub_label = {"ar": "➖ خصم رصيد", "en": "➖ Subtract Balance"}.get(lang, "➖ Subtract Balance")

    if is_banned:
        ban_label = {"ar": "✅ رفع الحظر", "en": "✅ Unban"}.get(lang, "✅ Unban")
    else:
        ban_label = {"ar": "🚫 حظر المستخدم", "en": "🚫 Ban User"}.get(lang, "🚫 Ban User")

    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=add_label, callback_data=AdminCallback(action="add_bal", target_id=target_telegram_id).pack()),
        InlineKeyboardButton(text=sub_label, callback_data=AdminCallback(action="sub_bal", target_id=target_telegram_id).pack()),
    )
    b.row(InlineKeyboardButton(text=ban_label, callback_data=AdminCallback(action="ban", target_id=target_telegram_id).pack()))
    b.row(_btn(get_text("btn_back", lang), AdminCallback(action="users")))
    return b.as_markup()


# ---------------------------------------------------------------------------
# Persistent reply keyboard (utility features only — never Recharge/Activy)
# ---------------------------------------------------------------------------

def dist_preview_pick_keyboard(
    distributors: List[Dict[str, Any]], lang: str,
) -> InlineKeyboardMarkup:
    """Admin picker: choose which distributor account to preview."""
    b = InlineKeyboardBuilder()
    for d in distributors:
        balance_fmt = f"{int(d.get('wallet_balance', 0) or 0):,}"
        label = f"{d['full_name']}  ({balance_fmt} DZD)"
        b.row(_btn(label, DistPreviewCallback(action="wallet", distributor_id=d["id"])))
    b.row(_btn(get_text("btn_back", lang), AdminCallback(action="panel")))
    return b.as_markup()


def dist_preview_wallet_keyboard(distributor_id: int, lang: str) -> InlineKeyboardMarkup:
    """Admin preview: wallet screen inline buttons."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("dist_self_btn_ledger", lang),
               DistPreviewCallback(action="ledger", distributor_id=distributor_id)))
    b.row(_btn(get_text("btn_back", lang), AdminCallback(action="panel")))
    return b.as_markup()


def dist_preview_ledger_keyboard(
    entries: List[Dict[str, Any]], distributor_id: int,
    page: int, total_pages: int, lang: str,
) -> InlineKeyboardMarkup:
    """Admin preview: paginated ledger for a distributor account."""
    b = InlineKeyboardBuilder()
    for e in entries:
        sign = "➕" if e["amount"] > 0 else "➖"
        abs_amount = abs(e["amount"])
        op_type = e.get("operation_type", "")
        if op_type == "admin_credit":
            op_label = get_text("dwlt_op_admin_credit", lang)
        elif op_type == "recharge_debit":
            op_label = get_text("dwlt_op_recharge_debit", lang)
        elif op_type == "admin_debit":
            op_label = get_text("dwlt_op_admin_debit", lang)
        else:
            op_label = get_text("dwlt_op_unknown", lang)
        date_short = e["created_at"][:10] if e.get("created_at") else "—"
        row_label = f"{sign} {abs_amount:,.0f}  {op_label}  {date_short}"
        b.row(InlineKeyboardButton(
            text=row_label,
            callback_data=DistPreviewCallback(
                action="entry", distributor_id=distributor_id,
                entry_id=e["id"], page=page,
            ).pack(),
        ))

    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=DistPreviewCallback(action="lpage", distributor_id=distributor_id, page=page - 1).pack(),
            ))
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=DistPreviewCallback(action="lpage", distributor_id=distributor_id, page=page).pack(),
        ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=DistPreviewCallback(action="lpage", distributor_id=distributor_id, page=page + 1).pack(),
            ))
        b.row(*nav_row)

    b.row(_btn(get_text("btn_back", lang),
               DistPreviewCallback(action="wallet", distributor_id=distributor_id)))
    return b.as_markup()


def dist_preview_entry_keyboard(
    distributor_id: int, entry_id: int, page: int, lang: str,
) -> InlineKeyboardMarkup:
    """Admin preview: back button from ledger entry detail."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("btn_back", lang),
               DistPreviewCallback(action="ledger", distributor_id=distributor_id, page=page)))
    return b.as_markup()


def distributor_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard for distributor-role users.
    Shows Wallet/Ledger instead of Balance/History/Games/Gift Cards/Favorites."""
    b = ReplyKeyboardBuilder()
    b.row(
        KeyboardButton(text=get_text("btn_dist_wallet", lang)),
        KeyboardButton(text=get_text("btn_dist_history", lang)),
    )
    b.row(
        KeyboardButton(text=get_text("btn_language", lang)),
        KeyboardButton(text=get_text("btn_help", lang)),
    )
    return b.as_markup(resize_keyboard=True)


def dist_self_wallet_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Inline buttons shown on the distributor's own wallet screen."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("dist_self_btn_ledger", lang), DistributorSelfCallback(action="ledger")))
    return b.as_markup()


def dist_self_ledger_keyboard(
    entries: List[Dict[str, Any]], page: int, total_pages: int, lang: str,
) -> InlineKeyboardMarkup:
    """Paginated ledger list for the distributor's own view."""
    b = InlineKeyboardBuilder()
    for e in entries:
        sign = "➕" if e["amount"] > 0 else "➖"
        abs_amount = abs(e["amount"])
        op_type = e.get("operation_type", "")
        if op_type == "admin_credit":
            op_label = get_text("dwlt_op_admin_credit", lang)
        elif op_type == "recharge_debit":
            op_label = get_text("dwlt_op_recharge_debit", lang)
        elif op_type == "admin_debit":
            op_label = get_text("dwlt_op_admin_debit", lang)
        else:
            op_label = get_text("dwlt_op_unknown", lang)
        date_short = e["created_at"][:10] if e.get("created_at") else "—"
        row_label = f"{sign} {abs_amount:,.0f}  {op_label}  {date_short}"
        b.row(InlineKeyboardButton(
            text=row_label,
            callback_data=DistributorSelfCallback(
                action="entry", entry_id=e["id"], page=page,
            ).pack(),
        ))

    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=DistributorSelfCallback(action="lpage", page=page - 1).pack(),
            ))
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=DistributorSelfCallback(action="lpage", page=page).pack(),
        ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=DistributorSelfCallback(action="lpage", page=page + 1).pack(),
            ))
        b.row(*nav_row)

    b.row(_btn(get_text("btn_back", lang), DistributorSelfCallback(action="wallet")))
    return b.as_markup()


def dist_self_ledger_entry_keyboard(entry_id: int, page: int, lang: str) -> InlineKeyboardMarkup:
    """Single back button shown on a distributor self-ledger entry detail screen."""
    b = InlineKeyboardBuilder()
    b.row(_btn(get_text("btn_back", lang), DistributorSelfCallback(action="ledger", page=page)))
    return b.as_markup()


def utility_reply_keyboard(lang: str, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Persistent bottom keyboard for non-recharge utility features.
    Recharge/Activy are intentionally excluded — those remain purely
    message-driven (phone*amount / phone only).
    """
    b = ReplyKeyboardBuilder()
    b.row(
        KeyboardButton(text=get_text("btn_balance", lang)),
        KeyboardButton(text=get_text("btn_history", lang)),
    )
    b.row(
        KeyboardButton(text=get_text("btn_games", lang)),
        KeyboardButton(text=get_text("btn_gift_cards", lang)),
    )
    b.row(
        KeyboardButton(text=get_text("btn_favorites", lang)),
    )
    b.row(
        KeyboardButton(text=get_text("btn_language", lang)),
        KeyboardButton(text=get_text("btn_help", lang)),
    )
    if is_admin:
        b.row(KeyboardButton(text=get_text("btn_admin", lang)))
    return b.as_markup(resize_keyboard=True)
