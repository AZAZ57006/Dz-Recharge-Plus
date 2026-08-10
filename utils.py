import re
import logging
from typing import Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Algerian phone validation
# ---------------------------------------------------------------------------

ALGERIAN_PHONE_RE = re.compile(r"^(0)(5|6|7)\d{8}$")
RECHARGE_RE = re.compile(r"^(0[5-7]\d{8})\*(\d{1,4})$")


def is_algerian_phone(text: str) -> bool:
    return bool(ALGERIAN_PHONE_RE.match(text.strip()))


def parse_recharge_input(text: str) -> Optional[Tuple[str, int]]:
    """Parse 'phone*amount' format. Returns (phone, amount) or None."""
    m = RECHARGE_RE.match(text.strip())
    if not m:
        return None
    phone, amount = m.group(1), int(m.group(2))
    if amount < 10 or amount > 5000:
        return None
    return phone, amount


def format_amount(amount: float) -> str:
    return f"{amount:,.0f} دج"


def format_amount_ledger(amount: float) -> str:
    """Ledger/wallet screens use 'DZD' (not the Arabic suffix) for consistency
    across both admin languages.  Never use this outside admin wallet/ledger UI."""
    return f"{amount:,.0f} DZD"


def format_datetime(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


# ---------------------------------------------------------------------------
# i18n texts — single source of truth for Arabic & English
# ---------------------------------------------------------------------------

TEXTS: dict[str, dict[str, str]] = {

    # ── Main menu ────────────────────────────────────────────────────────────
    "welcome": {
        "ar": (
            "👋 مرحباً <b>{name}</b>!\n\n"
            "أرسل رقم الهاتف والمبلغ لشحن رصيد، أو رقم الهاتف فقط لعرض عروض أكتيفي.\n\n"
            "📌 <b>أمثلة:</b>\n"
            "• <code>0661234567*100</code> — شحن 100 دج\n"
            "• <code>0661234567</code> — عرض عروض أكتيفي\n\n"
            "أوامر أخرى: /balance /history /language /help"
        ),
        "en": (
            "👋 Welcome, <b>{name}</b>!\n\n"
            "Send a phone number and amount to recharge, or just a phone number to see Activy offers.\n\n"
            "📌 <b>Examples:</b>\n"
            "• <code>0661234567*100</code> — top up 100 DZD\n"
            "• <code>0661234567</code> — show Activy offers\n\n"
            "Other commands: /balance /history /language /help"
        ),
    },
    "btn_games":       {"ar": "🕹️ ألعاب",               "en": "🕹️ Games"},
    "btn_gift_cards":  {"ar": "🎁 بطاقات هدايا",        "en": "🎁 Gift Cards"},
    "btn_balance":     {"ar": "💳 محفظتي",              "en": "💳 Wallet"},
    "btn_history":     {"ar": "🗂️ السجل",               "en": "🗂️ History"},
    "btn_language":    {"ar": "🌐 اللغة",               "en": "🌐 Language"},
    "btn_admin":       {"ar": "🛠️ الإدارة",             "en": "🛠️ Admin"},
    "btn_help":        {"ar": "❓ المساعدة",              "en": "❓ Help"},
    "btn_confirm":     {"ar": "✅ تأكيد",               "en": "✅ Confirm"},
    "btn_cancel":      {"ar": "❌ إلغاء",               "en": "❌ Cancel"},
    "btn_back":        {"ar": "🔙 رجوع",               "en": "🔙 Back"},
    "btn_deposit":     {"ar": "💳 طلب إيداع رصيد",     "en": "💳 Request Deposit"},
    "btn_retry":       {"ar": "🔄 إعادة المحاولة",     "en": "🔄 Retry"},

    # ── Help ─────────────────────────────────────────────────────────────────
    "help_text": {
        "ar": (
            "📖 <b>كيفية الاستخدام</b>\n\n"
            "• شحن عادي: أرسل <code>0661234567*100</code>\n"
            "• عروض أكتيفي: أرسل رقم الهاتف فقط <code>0661234567</code>\n"
            "• ألعاب وبطاقات: استخدم القائمة الرئيسية\n\n"
            "<b>الأوامر المتاحة:</b>\n"
            "/start — بدء البوت\n"
            "/balance — رصيدك\n"
            "/history — سجل المعاملات\n"
            "/language — تغيير اللغة\n"
        ),
        "en": (
            "📖 <b>How to use</b>\n\n"
            "• Standard recharge: send <code>0661234567*100</code>\n"
            "• Activy offers: send only a phone number <code>0661234567</code>\n"
            "• Games & gift cards: use the main menu\n\n"
            "<b>Available commands:</b>\n"
            "/start — Start the bot\n"
            "/balance — Check balance\n"
            "/history — Transaction history\n"
            "/language — Change language\n"
        ),
    },

    # ── Rate limiting ─────────────────────────────────────────────────────────
    "rate_limited": {
        "ar": "⏳ لقد تجاوزت الحد المسموح. حاول مرة أخرى بعد <b>{seconds}</b> ثانية.",
        "en": "⏳ Too many requests. Please try again in <b>{seconds}</b> seconds.",
    },

    # ── Processing ───────────────────────────────────────────────────────────
    "processing": {
        "ar": "⏳ جاري المعالجة، يرجى الانتظار...",
        "en": "⏳ Processing, please wait...",
    },

    # ── Asynchronous recharge tracking card ─────────────────────────────────
    "tracking_submitted": {
        "ar": (
            "⏳ <b>تم إرسال طلبك</b>\n\n"
            "🟡 قيد المعالجة — عادةً خلال ثوانٍ قليلة."
        ),
        "en": (
            "⏳ <b>Request Submitted</b>\n\n"
            "🟡 Processing — usually done within a few seconds."
        ),
    },
    "btn_check_now": {"ar": "🔄 تحقق الآن", "en": "🔄 Check Now"},
    "tracking_still_processing": {
        "ar": "🟡 لا تزال العملية قيد المعالجة. سيتم تحديث هذه الرسالة تلقائيًا عند الانتهاء.",
        "en": "🟡 Still processing. This message will update automatically once it's done.",
    },
    "tracking_ambiguous": {
        "ar": (
            "⚠️ <b>حالة غير مؤكدة</b>\n\n"
            "تمت إعادة تشغيل البوت أثناء معالجة هذا الطلب، ولا يمكننا تأكيد نتيجته تلقائيًا.\n"
            "يرجى التحقق من سجل المعاملات (الرصيد/السجل) قبل إعادة المحاولة."
        ),
        "en": (
            "⚠️ <b>Status Unconfirmed</b>\n\n"
            "The bot restarted while this request was being processed, so we can't "
            "automatically confirm its outcome.\n"
            "Please check your balance/history before retrying."
        ),
    },

    # ── Standard recharge ────────────────────────────────────────────────────
    "recharge_hint": {
        "ar": (
            "⚡ <b>شحن رصيد عادي</b>\n\n"
            "أرسل الرقم والمبلغ بالصيغة التالية:\n"
            "<code>رقم_الهاتف*المبلغ</code>\n\n"
            "مثال: <code>0661234567*100</code>\n\n"
            "📡 أو أرسل رقم الهاتف فقط لعرض عروض أكتيفي:\n"
            "<code>0661234567</code>"
        ),
        "en": (
            "⚡ <b>Standard Recharge</b>\n\n"
            "Send the phone number and amount:\n"
            "<code>phone*amount</code>\n\n"
            "Example: <code>0661234567*100</code>\n\n"
            "📡 Or send only a phone number to see Activy offers:\n"
            "<code>0661234567</code>"
        ),
    },
    "recharge_confirm": {
        "ar": (
            "🧾 <b>تأكيد الشحن</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} دج</b>\n\n"
            "هل تريد المتابعة؟"
        ),
        "en": (
            "🧾 <b>Confirm Recharge</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} DZD</b>\n\n"
            "Do you want to proceed?"
        ),
    },
    "recharge_success": {
        "ar": (
            "✅ <b>تم الشحن بنجاح</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} دج</b>\n"
            "💳 الرصيد: <b>{balance}</b>\n\n"
            "شكراً لاستخدامك RechargeDz Pro 🙏"
        ),
        "en": (
            "✅ <b>Recharge Successful</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} DZD</b>\n"
            "💳 Balance: <b>{balance}</b>\n\n"
            "Thank you for using RechargeDz Pro 🙏"
        ),
    },
    "recharge_failed": {
        "ar": "❌ فشل الشحن. يرجى المحاولة لاحقاً.",
        "en": "❌ Recharge failed. Please try again later.",
    },
    "recharge_failed_card": {
        "ar": (
            "❌ <b>فشلت العملية</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} دج</b>\n"
            "⚠️ {reason}\n\n"
            "لم يتم خصم أي رصيد من محفظتك."
        ),
        "en": (
            "❌ <b>Operation Failed</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} DZD</b>\n"
            "⚠️ {reason}\n\n"
            "No balance was deducted from your wallet."
        ),
    },
    "error_reason_provider": {
        "ar": "تعذر تنفيذ الطلب لدى مزود الخدمة.",
        "en": "The service provider could not complete the request.",
    },
    "error_reason_api": {
        "ar": "تعذر الاتصال بمزود الخدمة. حاول مرة أخرى.",
        "en": "Could not reach the service provider. Please try again.",
    },
    "recharge_cancelled": {
        "ar": "🚫 تم إلغاء العملية.",
        "en": "🚫 Operation cancelled.",
    },
    "insufficient_balance": {
        "ar": "⚠️ رصيدك غير كافٍ.\nرصيدك الحالي: <b>{balance}</b>\nالمطلوب: <b>{required} دج</b>",
        "en": "⚠️ Insufficient balance.\nYour balance: <b>{balance}</b>\nRequired: <b>{required} DZD</b>",
    },
    "invalid_offer": {
        "ar": "❌ العرض غير صالح.",
        "en": "❌ Invalid offer.",
    },

    # ── Activy ───────────────────────────────────────────────────────────────
    "activy_offers_title": {
        "ar": "🌐 <b>عروض أكتيفي</b>\nالرقم: <code>{phone}</code>\n\nاختر العرض المناسب:",
        "en": "🌐 <b>Activy Offers</b>\nPhone: <code>{phone}</code>\n\nSelect an offer:",
    },
    "activy_confirm": {
        "ar": (
            "🧾 <b>تأكيد عرض أكتيفي</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💰 <b>{price}</b>\n\n"
            "هل تريد المتابعة؟"
        ),
        "en": (
            "🧾 <b>Activy Offer Confirmation</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💰 <b>{price}</b>\n\n"
            "Do you want to proceed?"
        ),
    },
    "activy_success": {
        "ar": (
            "✅ <b>تم تفعيل العرض بنجاح</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💳 الرصيد: <b>{balance}</b>\n\n"
            "شكراً لاستخدامك RechargeDz Pro 🙏"
        ),
        "en": (
            "✅ <b>Offer Activated</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💳 Balance: <b>{balance}</b>\n\n"
            "Thank you for using RechargeDz Pro 🙏"
        ),
    },
    "activy_failed_card": {
        "ar": (
            "❌ <b>فشلت العملية</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "⚠️ {reason}\n\n"
            "لم يتم خصم أي رصيد من محفظتك."
        ),
        "en": (
            "❌ <b>Operation Failed</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "⚠️ {reason}\n\n"
            "No balance was deducted from your wallet."
        ),
    },
    "activy_duplicate_request": {
        "ar": "⚠️ تم استلام هذا الطلب بالفعل ويجري تنفيذه أو تم تنفيذه — لا يمكن تكراره.",
        "en": "⚠️ This request was already received and is being (or has been) processed — it can't be repeated.",
    },
    "activy_choose_operator": {
        "ar": "📡 تعذر التعرف تلقائيًا على مشغل الرقم <code>{phone}</code>.\nيرجى اختيار المشغل يدويًا:",
        "en": "📡 Could not automatically detect the operator for <code>{phone}</code>.\nPlease choose the operator manually:",
    },
    "activy_no_plans": {
        "ar": "⚠️ لا توجد عروض أكتيفي متاحة حاليًا لهذا المشغل. حاول لاحقًا.",
        "en": "⚠️ No Activy offers are currently available for this operator. Please try again later.",
    },

    # ── Favorite Numbers (address book) ─────────────────────────────────────
    "btn_favorites":         {"ar": "⭐ الأرقام المفضلة",     "en": "⭐ Favorite Numbers"},
    "btn_favorites_add":     {"ar": "➕ إضافة رقم جديد",      "en": "➕ Add New"},
    "btn_favorites_search":  {"ar": "🔍 بحث",                "en": "🔍 Search"},
    "btn_favorites_recharge": {"ar": "📱 شحن",               "en": "📱 Recharge"},
    "btn_favorites_activy":  {"ar": "📡 أكتيفي",             "en": "📡 Activy"},
    "btn_favorites_rename":  {"ar": "✏️ إعادة تسمية",        "en": "✏️ Rename"},
    "btn_favorites_delete":  {"ar": "🗑 حذف",                "en": "🗑 Delete"},
    "favorites_list_title": {
        "ar": "⭐ <b>الأرقام المفضلة</b>\n\n{entries}",
        "en": "⭐ <b>Favorite Numbers</b>\n\n{entries}",
    },
    "favorites_entry_line": {
        "ar": "👤 <b>{label}</b>\n📞 <code>{phone}</code>\n📶 {operator}\n",
        "en": "👤 <b>{label}</b>\n📞 <code>{phone}</code>\n📶 {operator}\n",
    },
    "favorites_empty": {
        "ar": "⭐ <b>الأرقام المفضلة</b>\n\nلا توجد أرقام محفوظة بعد.\nاضغط \"إضافة رقم جديد\" لحفظ أول رقم.",
        "en": "⭐ <b>Favorite Numbers</b>\n\nNo saved numbers yet.\nTap \"Add New\" to save your first number.",
    },
    "favorites_limit_reached": {
        "ar": "⚠️ لقد وصلت للحد الأقصى ({limit} رقم). احذف رقمًا لإضافة رقم جديد.",
        "en": "⚠️ You've reached the maximum of {limit} favorites. Delete one to add a new one.",
    },
    "favorites_ask_phone": {
        "ar": "📞 أرسل رقم الهاتف الذي تريد حفظه:",
        "en": "📞 Send the phone number you want to save:",
    },
    "favorites_ask_label": {
        "ar": "👤 أرسل اسمًا لهذا الرقم (مثال: أبي، المحل، المنزل):",
        "en": "👤 Send a name for this number (e.g. Father, Shop, Home):",
    },
    "favorites_already_saved": {
        "ar": "⚠️ هذا الرقم محفوظ بالفعل باسم <b>{label}</b>.",
        "en": "⚠️ This number is already saved as <b>{label}</b>.",
    },
    "favorites_invalid_phone": {
        "ar": "❌ رقم هاتف جزائري غير صالح. حاول مرة أخرى أو اضغط على أي زر آخر للإلغاء.",
        "en": "❌ Not a valid Algerian phone number. Try again, or tap any other button to cancel.",
    },
    "favorites_added": {
        "ar": "✅ تم حفظ <b>{label}</b> — <code>{phone}</code> في الأرقام المفضلة.",
        "en": "✅ Saved <b>{label}</b> — <code>{phone}</code> to your favorites.",
    },
    "favorites_ask_rename": {
        "ar": "✏️ أرسل الاسم الجديد لـ <b>{label}</b>:",
        "en": "✏️ Send the new name for <b>{label}</b>:",
    },
    "favorites_renamed": {
        "ar": "✅ تم تغيير الاسم إلى <b>{label}</b>.",
        "en": "✅ Renamed to <b>{label}</b>.",
    },
    "favorites_delete_confirm": {
        "ar": "🗑 هل تريد حذف <b>{label}</b> — <code>{phone}</code>؟",
        "en": "🗑 Delete <b>{label}</b> — <code>{phone}</code>?",
    },
    "favorites_deleted": {
        "ar": "🗑 تم حذف <b>{label}</b>.",
        "en": "🗑 <b>{label}</b> has been deleted.",
    },
    "favorites_not_found": {
        "ar": "⚠️ هذا الرقم المفضل لم يعد موجودًا.",
        "en": "⚠️ This favorite no longer exists.",
    },
    "favorites_ask_search": {
        "ar": "🔍 أرسل جزءًا من الاسم أو الرقم للبحث:",
        "en": "🔍 Send part of a name or number to search:",
    },
    "favorites_search_results": {
        "ar": "🔍 <b>نتائج البحث عن</b> \"{query}\"\n\n{entries}",
        "en": "🔍 <b>Search results for</b> \"{query}\"\n\n{entries}",
    },
    "favorites_search_empty": {
        "ar": "🔍 لا توجد نتائج مطابقة لـ \"{query}\".",
        "en": "🔍 No favorites match \"{query}\".",
    },
    "favorites_ask_amount": {
        "ar": "💳 أرسل المبلغ لشحن <b>{label}</b> — <code>{phone}</code>:",
        "en": "💳 Send the amount to recharge <b>{label}</b> — <code>{phone}</code>:",
    },

    # ── Wallet ───────────────────────────────────────────────────────────────
    "wallet_title": {
        "ar": (
            "💳 <b>محفظتي</b>\n\n"
            "رصيدك الحالي\n"
            "💰 <b>{balance}</b>\n\n"
            "اضغط على الزر أدناه لطلب إيداع رصيد."
        ),
        "en": (
            "💳 <b>My Wallet</b>\n\n"
            "Current Balance\n"
            "💰 <b>{balance}</b>\n\n"
            "Tap below to request a deposit."
        ),
    },
    "balance_msg": {
        "ar": "💳 <b>رصيدك الحالي</b>\n\n💰 <b>{balance}</b>",
        "en": "💳 <b>Your Balance</b>\n\n💰 <b>{balance}</b>",
    },

    # ── Deposit ──────────────────────────────────────────────────────────────
    "deposit_select_amount": {
        "ar": "💳 <b>طلب إيداع</b>\n\nاختر المبلغ:",
        "en": "💳 <b>Request Deposit</b>\n\nSelect an amount:",
    },
    "deposit_confirm_msg": {
        "ar": (
            "🧾 <b>تأكيد طلب الإيداع</b>\n\n"
            "💰 <b>{amount}</b>\n\n"
            "هل تريد المتابعة؟"
        ),
        "en": (
            "🧾 <b>Confirm Deposit Request</b>\n\n"
            "💰 <b>{amount}</b>\n\n"
            "Do you want to proceed?"
        ),
    },
    "deposit_requested": {
        "ar": "✅ <b>تم إرسال طلب الإيداع</b>\n\n💰 <b>{amount}</b>\n\nسيتم إخطارك عند الموافقة.",
        "en": "✅ <b>Deposit Request Sent</b>\n\n💰 <b>{amount}</b>\n\nYou'll be notified when it's approved.",
    },
    "deposit_approved_user": {
        "ar": "🎉 <b>تمت الموافقة على الإيداع</b>\n\n💰 <b>{amount}</b>\n💳 الرصيد الجديد: <b>{balance}</b>",
        "en": "🎉 <b>Deposit Approved</b>\n\n💰 <b>{amount}</b>\n💳 New Balance: <b>{balance}</b>",
    },
    "deposit_rejected_user": {
        "ar": "❌ <b>تم رفض طلب الإيداع</b>\n\n💰 <b>{amount}</b>\n\nيرجى التواصل مع الدعم.",
        "en": "❌ <b>Deposit Rejected</b>\n\n💰 <b>{amount}</b>\n\nPlease contact support.",
    },

    # ── History ──────────────────────────────────────────────────────────────
    "history_empty": {
        "ar": "📜 لا توجد معاملات سابقة.",
        "en": "📜 No transactions yet.",
    },
    "history_title": {
        "ar": "📜 <b>آخر معاملاتك</b>",
        "en": "📜 <b>Your Recent Transactions</b>",
    },
    "history_row": {
        "ar": "• {type} | {desc} | {amount} دج | {status} | {date}",
        "en": "• {type} | {desc} | {amount} DZD | {status} | {date}",
    },

    # ── Professional Transaction History ────────────────────────────────────
    "btn_history_filter":       {"ar": "🔎 تصفية",        "en": "🔎 Filter"},
    "btn_history_clear_filter": {"ar": "🧹 إزالة التصفية", "en": "🧹 Clear Filter"},
    "btn_history_search":       {"ar": "🔍 بحث",           "en": "🔍 Search"},
    "btn_history_clear_search": {"ar": "🧹 إزالة البحث",   "en": "🧹 Clear Search"},
    "btn_history_repeat":       {"ar": "🔁 إعادة",         "en": "🔁 Repeat"},
    "btn_history_add_favorite": {"ar": "⭐ إضافة للمفضلة", "en": "⭐ Add to Favorites"},
    "btn_history_copy_ref":     {"ar": "📋 نسخ المرجع",    "en": "📋 Copy Reference"},

    "history_list_title": {
        "ar": "📜 <b>سجل معاملاتك</b> ({count})\n\nاضغط على أي معاملة لعرض التفاصيل.",
        "en": "📜 <b>Your Transaction History</b> ({count})\n\nTap any transaction to view details.",
    },
    "history_filter_title": {
        "ar": "🔎 <b>تصفية السجل</b>\n\nاختر فلترة التاريخ والحالة (يمكن الجمع بينهما):",
        "en": "🔎 <b>Filter History</b>\n\nChoose a date and/or status filter (combinable):",
    },
    "history_ask_search": {
        "ar": "🔍 أرسل رقم الهاتف أو اسم المفضلة للبحث في السجل:",
        "en": "🔍 Send a phone number or favorite name to search your history:",
    },
    "history_not_found": {
        "ar": "⚠️ هذه المعاملة لم تعد موجودة.",
        "en": "⚠️ This transaction no longer exists.",
    },
    "history_no_favorite": {"ar": "—", "en": "—"},
    "history_no_reference": {
        "ar": "لا يوجد",
        "en": "None",
    },
    "history_details": {
        "ar": (
            "📄 <b>تفاصيل المعاملة</b>\n\n"
            "📞 الهاتف: <code>{phone}</code>\n"
            "👤 المفضلة: {favorite}\n"
            "📶 المشغل: {operator}\n"
            "🧾 النوع: {type}\n"
            "💰 المبلغ/العرض: <b>{amount} دج</b>\n"
            "🕒 التاريخ: {date}\n"

            "📊 الحالة: {status}"
        ),
        "en": (
            "📄 <b>Transaction Details</b>\n\n"
            "📞 Phone: <code>{phone}</code>\n"
            "👤 Favorite: {favorite}\n"
            "📶 Operator: {operator}\n"
            "🧾 Type: {type}\n"
            "💰 Amount/Offer: <b>{amount} DZD</b>\n"
            "🕒 Date: {date}\n"
            "🔖 OneClick Reference: <code>{reference}</code>\n"
            "📊 Status: {status}"
        ),
    },
    "history_ref_copied": {
        "ar": "📋 المرجع أدناه — اضغط مطولاً للنسخ.",
        "en": "📋 Reference sent below — tap and hold to copy.",
    },
    "history_repeat_unsupported": {
        "ar": "⚠️ لا يمكن إعادة هذا النوع من المعاملات.",
        "en": "⚠️ This transaction type can't be repeated.",
    },
    "history_closed": {
        "ar": "📜 تم إغلاق السجل.",
        "en": "📜 History closed.",
    },
    "history_type_standard":  {"ar": "شحن عادي",       "en": "Standard Recharge"},
    "history_type_activy":    {"ar": "أكتيفي",          "en": "Activy"},
    "history_type_game":      {"ar": "شحن لعبة",        "en": "Game Top-up"},
    "history_type_gift_card": {"ar": "بطاقة هدايا",     "en": "Gift Card"},
    "filter_today":     {"ar": "📅 اليوم",          "en": "📅 Today"},
    "filter_yesterday": {"ar": "📅 أمس",            "en": "📅 Yesterday"},
    "filter_7days":     {"ar": "📅 آخر 7 أيام",     "en": "📅 Last 7 Days"},
    "filter_success":   {"ar": "✅ ناجحة",          "en": "✅ Success"},
    "filter_failed":    {"ar": "❌ فاشلة",          "en": "❌ Failed"},

    # ── Games ────────────────────────────────────────────────────────────────
    "games_menu":  {"ar": "🎮 <b>شحن الألعاب</b>\n\nاختر اللعبة:",   "en": "🎮 <b>Games Recharge</b>\n\nSelect a game:"},
    "game_amounts":{"ar": "🎮 <b>{game}</b>\n\nاختر الكمية:",         "en": "🎮 <b>{game}</b>\n\nSelect amount:"},
    "game_confirm": {
        "ar": (
            "🎮 <b>تأكيد شحن اللعبة</b>\n\n"
            "🕹️ {game}\n"
            "🔢 <b>{amount} {currency}</b>\n"
            "💰 <b>{price} دج</b>\n\n"
            "هل تريد المتابعة؟"
        ),
        "en": (
            "🎮 <b>Confirm Game Top-up</b>\n\n"
            "🕹️ {game}\n"
            "🔢 <b>{amount} {currency}</b>\n"
            "💰 <b>{price} DZD</b>\n\n"
            "Do you want to proceed?"
        ),
    },
    "game_success": {
        "ar": (
            "✅ <b>تم الشحن بنجاح</b>\n\n"
            "🕹️ {game}\n"
            "🔢 <b>{amount} {currency}</b>\n"
            "🔖 <code>{ref}</code>"
        ),
        "en": (
            "✅ <b>Top-up Successful</b>\n\n"
            "🕹️ {game}\n"
            "🔢 <b>{amount} {currency}</b>\n"
            "🔖 <code>{ref}</code>"
        ),
    },

    # ── Gift cards ────────────────────────────────────────────────────────────
    "gift_cards_menu": {"ar": "🎁 <b>بطاقات الهدايا</b>\n\nاختر النوع:", "en": "🎁 <b>Gift Cards</b>\n\nSelect type:"},
    "gift_amounts":    {"ar": "🎁 <b>{card}</b>\n\nاختر القيمة:",         "en": "🎁 <b>{card}</b>\n\nSelect value:"},
    "gift_confirm": {
        "ar": (
            "🎁 <b>تأكيد بطاقة الهدية</b>\n\n"
            "🏷️ {card}\n"
            "💰 <b>{amount} دج</b>\n\n"
            "هل تريد المتابعة؟"
        ),
        "en": (
            "🎁 <b>Confirm Gift Card</b>\n\n"
            "🏷️ {card}\n"
            "💰 <b>{amount} DZD</b>\n\n"
            "Do you want to proceed?"
        ),
    },
    "gift_success": {
        "ar": (
            "✅ <b>تم الشراء بنجاح</b>\n\n"
            "🏷️ {card}\n"
            "💰 <b>{amount} دج</b>\n"
            "🔑 <code>{code}</code>"
        ),
        "en": (
            "✅ <b>Purchase Successful</b>\n\n"
            "🏷️ {card}\n"
            "💰 <b>{amount} DZD</b>\n"
            "🔑 <code>{code}</code>"
        ),
    },

    # ── Language ─────────────────────────────────────────────────────────────
    "choose_language": {
        "ar": "🌍 اختر اللغة / Choose Language:",
        "en": "🌍 Choose Language / اختر اللغة:",
    },
    "language_changed": {
        "ar": "✅ تم تغيير اللغة إلى العربية.",
        "en": "✅ Language changed to English.",
    },

    # ── Admin panel ──────────────────────────────────────────────────────────
    "admin_panel": {
        "ar": "⚙️ <b>لوحة الإدارة</b>\n\nاختر الإجراء:",
        "en": "⚙️ <b>Admin Panel</b>\n\nSelect an action:",
    },
    "admin_stats": {
        "ar": (
            "📊 <b>إحصائيات البوت</b>\n\n"
            "👥 المستخدمون: <b>{users}</b>\n"
            "💳 المعاملات: <b>{transactions}</b>\n"
            "✅ الناجحة: <b>{success}</b>\n"
            "❌ الفاشلة: <b>{failed}</b>\n"
            "⏳ طلبات الإيداع: <b>{deposits}</b>\n"
            "🔧 وضع المحاكاة: <b>{mock}</b>"
        ),
        "en": (
            "📊 <b>Bot Statistics</b>\n\n"
            "👥 Users: <b>{users}</b>\n"
            "💳 Transactions: <b>{transactions}</b>\n"
            "✅ Successful: <b>{success}</b>\n"
            "❌ Failed: <b>{failed}</b>\n"
            "⏳ Pending Deposits: <b>{deposits}</b>\n"
            "🔧 Mock Mode: <b>{mock}</b>"
        ),
    },
    "admin_not_authorized": {
        "ar": "🚫 ليس لديك صلاحية الوصول إلى لوحة الإدارة.",
        "en": "🚫 You are not authorized to access the admin panel.",
    },
    "mock_on":  {"ar": "✅ مفعّل", "en": "✅ ON"},
    "mock_off": {"ar": "❌ معطّل", "en": "❌ OFF"},

    # Admin — users
    "admin_users_empty":   {"ar": "👥 لا يوجد مستخدمون بعد.",   "en": "👥 No users yet."},
    "admin_users_title":   {"ar": "👥 <b>قائمة المستخدمين</b>\n", "en": "👥 <b>Users List</b>\n"},
    "admin_users_unknown": {"ar": "مجهول",                        "en": "Unknown"},

    # Admin — logs
    "admin_logs_empty": {"ar": "📜 لا توجد سجلات بعد.",    "en": "📜 No logs yet."},
    "admin_logs_title": {"ar": "📜 <b>آخر السجلات</b>\n",  "en": "📜 <b>Recent Logs</b>\n"},

    # Admin — transactions
    "admin_txns_empty": {"ar": "💳 لا توجد معاملات بعد.",    "en": "💳 No transactions yet."},
    "admin_txns_title": {"ar": "💳 <b>آخر المعاملات</b>\n",  "en": "💳 <b>Recent Transactions</b>\n"},

    # Admin — deposit management
    "admin_deposits_empty": {
        "ar": "💳 لا توجد طلبات إيداع معلقة.",
        "en": "💳 No pending deposit requests.",
    },
    "admin_deposits_title": {
        "ar": "💳 <b>طلبات الإيداع المعلقة</b>\n",
        "en": "💳 <b>Pending Deposit Requests</b>\n",
    },
    "admin_deposit_row": {
        "ar": "• #{id} | {name} | <b>{amount} دج</b> | {date}",
        "en": "• #{id} | {name} | <b>{amount} DZD</b> | {date}",
    },
    "admin_deposit_approved": {
        "ar": "✅ تمت الموافقة على الإيداع #{id} للمستخدم {name} ({amount} دج).",
        "en": "✅ Deposit #{id} for {name} ({amount} DZD) approved.",
    },
    "admin_deposit_rejected": {
        "ar": "❌ تم رفض الإيداع #{id} للمستخدم {name} ({amount} دج).",
        "en": "❌ Deposit #{id} for {name} ({amount} DZD) rejected.",
    },
    "admin_deposit_already_resolved": {
        "ar": "⚠️ هذا الطلب تمت معالجته بالفعل.",
        "en": "⚠️ This request has already been resolved.",
    },
    "admin_deposit_not_found": {
        "ar": "❌ طلب الإيداع غير موجود.",
        "en": "❌ Deposit request not found.",
    },
    # Admin notification to user about their deposit
    "admin_deposit_notify_title": {
        "ar": "💳 <b>طلب إيداع جديد</b>\n\nالمستخدم: <b>{name}</b>\nالمعرّف: <code>{uid}</code>\nالمبلغ: <b>{amount}</b>",
        "en": "💳 <b>New Deposit Request</b>\n\nUser: <b>{name}</b>\nID: <code>{uid}</code>\nAmount: <b>{amount}</b>",
    },

    # Admin — balance management
    "admin_add_balance_ask_id": {
        "ar": "💰 <b>إضافة رصيد</b>\nأرسل معرّف تيليغرام للمستخدم (رقم):",
        "en": "💰 <b>Add Balance</b>\nSend the user's Telegram ID (number):",
    },
    "admin_add_balance_ask_amount": {
        "ar": "✅ المستخدم: <b>{name}</b>\nالرصيد الحالي: <b>{balance} دج</b>\n\nأرسل المبلغ الذي تريد إضافته:",
        "en": "✅ User: <b>{name}</b>\nCurrent balance: <b>{balance} DZD</b>\n\nSend the amount to add:",
    },
    "admin_balance_added": {
        "ar": "✅ تمت إضافة <b>{amount} دج</b> للمستخدم <code>{uid}</code>\nالرصيد الجديد: <b>{balance} دج</b>",
        "en": "✅ Added <b>{amount} DZD</b> to user <code>{uid}</code>\nNew balance: <b>{balance} DZD</b>",
    },
    "admin_sub_balance_ask_id": {
        "ar": "➖ <b>خصم رصيد</b>\nأرسل معرّف تيليغرام للمستخدم (رقم):",
        "en": "➖ <b>Subtract Balance</b>\nSend the user's Telegram ID (number):",
    },
    "admin_sub_balance_ask_amount": {
        "ar": "✅ المستخدم: <b>{name}</b>\nالرصيد الحالي: <b>{balance} دج</b>\n\nأرسل المبلغ الذي تريد خصمه:",
        "en": "✅ User: <b>{name}</b>\nCurrent balance: <b>{balance} DZD</b>\n\nSend the amount to subtract:",
    },
    "admin_balance_subtracted": {
        "ar": "✅ تم خصم <b>{amount} دج</b> من المستخدم <code>{uid}</code>\nالرصيد الجديد: <b>{balance} دج</b>",
        "en": "✅ Subtracted <b>{amount} DZD</b> from user <code>{uid}</code>\nNew balance: <b>{balance} DZD</b>",
    },

    # Admin — broadcast
    "admin_broadcast_ask": {
        "ar": "📢 <b>رسالة جماعية</b>\nأرسل الرسالة التي تريد إرسالها لجميع المستخدمين:",
        "en": "📢 <b>Broadcast</b>\nSend the message to broadcast to all users:",
    },
    "admin_broadcast_done": {
        "ar": "📢 اكتمل الإرسال!\n✅ تم الإرسال: {sent}\n❌ فشل: {failed}",
        "en": "📢 Broadcast complete!\n✅ Sent: {sent}\n❌ Failed: {failed}",
    },

    # Admin — ban/unban
    "admin_ban_done":   {"ar": "🚫 تم حظر المستخدم.\nالمعرّف: <code>{uid}</code>",          "en": "🚫 User banned.\nID: <code>{uid}</code>"},
    "admin_unban_done": {"ar": "✅ تم رفع الحظر عن المستخدم.\nالمعرّف: <code>{uid}</code>", "en": "✅ User unbanned.\nID: <code>{uid}</code>"},
    "admin_user_not_found": {"ar": "❌ لم يتم العثور على المستخدم.", "en": "❌ User not found."},

    # Admin — mock toggle
    "admin_mock_toggled": {"ar": "🔧 وضع المحاكاة: <b>{status}</b>", "en": "🔧 Mock mode: <b>{status}</b>"},

    # Admin — /validate command
    "validate_checking": {
        "ar": "🔑 جاري التحقق من مفتاح API...",
        "en": "🔑 Checking API key...",
    },
    "validate_valid": {
        "ar": (
            "✅ <b>مفتاح API صالح</b>\n\n"
            "👤 الحساب: <code>{username}</code>\n"
            "🔧 النوع: <b>{key_type}</b>\n"
            "📋 الصلاحية: <b>{scope}</b>\n"
            "💰 الرصيد: <b>{balance}</b>\n"
            "🌐 الرابط: <code>{url}</code>"
        ),
        "en": (
            "✅ <b>API Key Valid</b>\n\n"
            "👤 Account: <code>{username}</code>\n"
            "🔧 Type: <b>{key_type}</b>\n"
            "📋 Scope: <b>{scope}</b>\n"
            "💰 Balance: <b>{balance}</b>\n"
            "🌐 URL: <code>{url}</code>"
        ),
    },
    "validate_invalid": {
        "ar": "❌ <b>مفتاح API غير صالح</b>\n\nالخطأ: <code>{error}</code>",
        "en": "❌ <b>API Key Invalid</b>\n\nError: <code>{error}</code>",
    },
    "validate_balance_unavailable": {
        "ar": "غير متاح",
        "en": "Unavailable",
    },

    # Admin keyboard button labels
    "admin_btn_stats":        {"ar": "📊 إحصائيات",       "en": "📊 Stats"},
    "admin_btn_users":        {"ar": "👥 المستخدمون",      "en": "👥 Users"},
    "admin_btn_logs":         {"ar": "📜 السجلات",         "en": "📜 Logs"},
    "admin_btn_txns":         {"ar": "💳 المعاملات",       "en": "💳 Transactions"},
    "admin_btn_add_balance":  {"ar": "💰 إضافة رصيد",      "en": "💰 Add Balance"},
    "admin_btn_broadcast":    {"ar": "📢 رسالة جماعية",    "en": "📢 Broadcast"},
    "admin_btn_deposits":     {"ar": "💳 طلبات الإيداع",   "en": "💳 Deposit Requests"},
    "admin_btn_dashboard":    {"ar": "📊 لوحة التحكم",     "en": "📊 Dashboard"},

    # Admin Dashboard (read-only reporting/diagnostics)
    "dash_btn_stats":         {"ar": "📈 الإحصائيات",      "en": "📈 Statistics"},
    "dash_btn_diagnostics":   {"ar": "🩺 التشخيص",         "en": "🩺 Diagnostics"},
    "dash_btn_alerts":        {"ar": "🚨 التنبيهات",       "en": "🚨 Alerts"},
    "dash_btn_refresh":       {"ar": "🔄 تحديث",           "en": "🔄 Refresh"},
    "dash_refreshed":         {"ar": "✅ تم التحديث — لا تغييرات جديدة", "en": "✅ Refreshed — no new changes"},
    "dash_period_today":      {"ar": "اليوم",              "en": "Today"},
    "dash_period_yesterday":  {"ar": "الأمس",              "en": "Yesterday"},
    "dash_period_7days":      {"ar": "آخر 7 أيام",         "en": "Last 7 Days"},
    "dash_period_30days":     {"ar": "آخر 30 يوم",         "en": "Last 30 Days"},

    "dash_home_title": {
        "ar": "📊 <b>لوحة تحكم المدير</b>\n\n"
              "🩺 <b>مؤشر الصحة العام:</b> {score}% {score_emoji}\n"
              "{reasons}\n"
              "👥 إجمالي المستخدمين: {total_users}\n"
              "📜 سجل المعاملات: {total_tx}\n"
              "🟡 معاملات قيد المعالجة: {processing_tx}\n"
              "📅 اليوم: {today_success} ناجحة / {today_failed} فاشلة — 💰 {today_sales} دج\n"
              "{action_required}"
              "{last_activity}",
        "en": "📊 <b>Admin Dashboard</b>\n\n"
              "🩺 <b>Health Score:</b> {score}% {score_emoji}\n"
              "{reasons}\n"
              "👥 Total Users: {total_users}\n"
              "📜 Transaction History: {total_tx}\n"
              "🟡 Processing Transactions: {processing_tx}\n"
              "📅 Today: {today_success} success / {today_failed} failed — 💰 {today_sales} DZD\n"
              "{action_required}"
              "{last_activity}",
    },
    "dash_health_reason_line": {"ar": "   • {reason}", "en": "   • {reason}"},
    "dash_health_all_good": {
        "ar": "   ✅ كل الأنظمة تعمل بشكل طبيعي",
        "en": "   ✅ All systems operating normally",
    },
    "dash_action_required_title": {
        "ar": "\n⚠️ <b>إجراء مطلوب:</b>\n",
        "en": "\n⚠️ <b>Action Required:</b>\n",
    },
    "dash_action_required_line": {"ar": "   • {message}", "en": "   • {message}"},
    "dash_last_activity_title": {
        "ar": "\n🕐 <b>آخر نشاط:</b>\n",
        "en": "\n🕐 <b>Last Activity:</b>\n",
    },
    "dash_last_activity_line": {
        "ar": "   {time} — {operator} — {phone} — {detail}",
        "en": "   {time} — {operator} — {phone} — {detail}",
    },
    "dash_last_activity_none": {
        "ar": "\n🕐 <b>آخر نشاط:</b> لا يوجد بعد\n",
        "en": "\n🕐 <b>Last Activity:</b> none yet\n",
    },

    "dash_stats_title": {
        "ar": "📈 <b>الإحصائيات — {label}</b>\n\n"
              "✅ ناجحة: {success}\n"
              "❌ فاشلة: {failed}\n"
              "💰 إجمالي المبيعات: {sales} دج\n"
              "👥 مستخدمون جدد: {new_users}\n"
              "⭐ أرقام مفضلة جديدة: {new_favorites}\n\n"
              "📡 حسب المشغل:\n{by_operator}\n"
              "🏷 حسب النوع:\n{by_type}",
        "en": "📈 <b>Statistics — {label}</b>\n\n"
              "✅ Success: {success}\n"
              "❌ Failed: {failed}\n"
              "💰 Total Sales: {sales} DZD\n"
              "👥 New Users: {new_users}\n"
              "⭐ New Favorites: {new_favorites}\n\n"
              "📡 By Operator:\n{by_operator}\n"
              "🏷 By Type:\n{by_type}",
    },
    "dash_stats_breakdown_line": {"ar": "   • {label}: {count}", "en": "   • {label}: {count}"},
    "dash_stats_breakdown_empty": {"ar": "   —", "en": "   —"},

    "dash_diagnostics_title": {
        "ar": "🩺 <b>التشخيص</b>\n\n"
              "🔌 حالة OneClick: {reachable}\n"
              "💰 رصيد المحفظة: {wallet}\n"
              "⏱ متوسط زمن الاستجابة: {avg_response}\n"
              "📡 عدد العروض المتاحة:\n{offer_counts}\n"
              "📉 نسبة الفشل ({hours} ساعة): {failure_pct}% ({failed}/{total})\n"
              "🕐 آخر تحقق: {last_checked}",
        "en": "🩺 <b>Diagnostics</b>\n\n"
              "🔌 OneClick status: {reachable}\n"
              "💰 Wallet balance: {wallet}\n"
              "⏱ Avg response time: {avg_response}\n"
              "📡 Live offer counts:\n{offer_counts}\n"
              "📉 Failure rate (last {hours}h): {failure_pct}% ({failed}/{total})\n"
              "🕐 Last checked: {last_checked}",
    },
    "dash_status_online":  {"ar": "🟢 متصل", "en": "🟢 Online"},
    "dash_status_offline": {"ar": "🔴 غير متصل ({error})", "en": "🔴 Offline ({error})"},

    "dash_alerts_title": {
        "ar": "🚨 <b>التنبيهات النشطة</b>\n\n{alerts}",
        "en": "🚨 <b>Active Alerts</b>\n\n{alerts}",
    },
    "dash_alerts_none": {
        "ar": "✅ لا توجد تنبيهات نشطة — كل الأنظمة تعمل بشكل طبيعي.",
        "en": "✅ No active alerts — all systems operating normally.",
    },
    "dash_alert_line_critical": {"ar": "🔴 {message}", "en": "🔴 {message}"},
    "dash_alert_line_warning":  {"ar": "🟡 {message}", "en": "🟡 {message}"},

    "admin_btn_ops_center": {"ar": "🎛 مركز العمليات", "en": "🎛 Operations Center"},

    # Distributor Management System — Phase 1 (Foundation)
    "admin_btn_distributors": {"ar": "🧑‍💼 الموزعون", "en": "🧑‍💼 Distributors"},
    "dadm_btn_create":  {"ar": "➕ إضافة موزع", "en": "➕ Create Distributor"},
    "dadm_btn_list":    {"ar": "📋 قائمة الموزعين", "en": "📋 Distributor List"},
    "dadm_btn_search":  {"ar": "🔎 بحث عن موزع", "en": "🔎 Search Distributor"},
    "dadm_btn_suspend": {"ar": "⛔ تعليق", "en": "⛔ Suspend"},
    "dadm_btn_activate": {"ar": "✅ تفعيل", "en": "✅ Activate"},

    "dadm_menu_title": {
        "ar": "🧑‍💼 <b>إدارة الموزعين</b>\n\nاختر إجراءً:",
        "en": "🧑‍💼 <b>Distributor Management</b>\n\nSelect an action:",
    },

    "dadm_ask_telegram_id": {
        "ar": "🆔 أدخل معرف تيليجرام (Telegram ID) الخاص بالموزع الجديد:",
        "en": "🆔 Enter the new distributor's Telegram ID:",
    },
    "dadm_invalid_telegram_id": {
        "ar": "⚠️ معرف تيليجرام غير صالح. أدخل رقماً صحيحاً.",
        "en": "⚠️ Invalid Telegram ID. Please enter a numeric ID.",
    },
    "dadm_ask_full_name": {
        "ar": "👤 أدخل الاسم الكامل للموزع:",
        "en": "👤 Enter the distributor's full name:",
    },
    "dadm_ask_phone": {
        "ar": "📞 أدخل رقم هاتف الموزع (أو أرسل - لتخطي):",
        "en": "📞 Enter the distributor's phone number (or send - to skip):",
    },
    "dadm_already_exists": {
        "ar": "⚠️ هذا المستخدم مسجل بالفعل كموزع.",
        "en": "⚠️ This user is already registered as a distributor.",
    },
    "dadm_created": {
        "ar": "✅ <b>تم إنشاء الموزع</b>\n👤 {name}\n🆔 <code>{telegram_id}</code>\n📞 {phone}",
        "en": "✅ <b>Distributor Created</b>\n👤 {name}\n🆔 <code>{telegram_id}</code>\n📞 {phone}",
    },

    "dadm_ask_search": {
        "ar": "🔍 أدخل الاسم أو رقم الهاتف أو معرف تيليجرام للبحث:",
        "en": "🔍 Enter a name, phone number, or Telegram ID to search:",
    },

    "dadm_list_title":   {"ar": "📋 <b>قائمة الموزعين</b> ({count})", "en": "📋 <b>Distributor List</b> ({count})"},
    "dadm_list_empty":   {"ar": "لا يوجد موزعون بعد.", "en": "No distributors yet."},
    "dadm_search_empty": {"ar": "لا توجد نتائج مطابقة.", "en": "No matching results."},

    "dadm_detail": {
        "ar": (
            "🧑‍💼 <b>{name}</b>\n"
            "🆔 <code>{telegram_id}</code>\n"
            "📞 {phone}\n"
            "💳 {balance}\n"
            "📌 {status}\n"
            "🕐 {last_activity}\n"
            "📅 {created_at}"
        ),
        "en": (
            "🧑‍💼 <b>{name}</b>\n"
            "🆔 <code>{telegram_id}</code>\n"
            "📞 {phone}\n"
            "💳 {balance}\n"
            "📌 {status}\n"
            "🕐 {last_activity}\n"
            "📅 {created_at}"
        ),
    },
    "dadm_status_active":    {"ar": "🟢 نشط", "en": "🟢 Active"},
    "dadm_status_suspended": {"ar": "🔴 معلق", "en": "🔴 Suspended"},
    "dadm_no_activity":      {"ar": "لا يوجد نشاط بعد", "en": "No activity yet"},
    "dadm_not_found":        {"ar": "❌ لم يتم العثور على الموزع.", "en": "❌ Distributor not found."},
    "dadm_status_updated":   {"ar": "✅ تم تحديث حالة الموزع.", "en": "✅ Distributor status updated."},

    # Distributor Wallet & Ledger — Phase 2 (admin-only screens)
    "dadm_btn_wallet": {"ar": "💳 المحفظة", "en": "💳 Wallet"},

    "dwlt_menu_title": {
        "ar": "💳 <b>محفظة {name}</b>\n\n💰 الرصيد الحالي: <b>{balance}</b>",
        "en": "💳 <b>{name}'s Wallet</b>\n\n💰 Current balance: <b>{balance}</b>",
    },
    "dwlt_btn_credit": {"ar": "➕ إيداع",       "en": "➕ Credit"},
    "dwlt_btn_debit":  {"ar": "➖ خصم",         "en": "➖ Debit"},
    "dwlt_btn_ledger": {"ar": "📋 سجل الحركات", "en": "📋 Ledger"},

    "dwlt_ask_credit_amount": {
        "ar": "💬 أدخل المبلغ المراد إيداعه (DZD):",
        "en": "💬 Enter the amount to credit (DZD):",
    },
    "dwlt_ask_debit_amount": {
        "ar": "💬 أدخل المبلغ المراد خصمه (DZD):",
        "en": "💬 Enter the amount to debit (DZD):",
    },
    "dwlt_invalid_amount": {
        "ar": "⚠️ مبلغ غير صالح. أدخل رقماً موجباً.",
        "en": "⚠️ Invalid amount. Enter a positive number.",
    },

    "dwlt_confirm_credit": {
        "ar": (
            "➕ <b>تأكيد الإيداع</b>\n\n"
            "👤 {name}\n"
            "💰 المبلغ: <b>+{amount}</b>\n"
            "💳 الرصيد: {before} → <b>{after}</b>"
        ),
        "en": (
            "➕ <b>Confirm Credit</b>\n\n"
            "👤 {name}\n"
            "💰 Amount: <b>+{amount}</b>\n"
            "💳 Balance: {before} → <b>{after}</b>"
        ),
    },
    "dwlt_confirm_debit": {
        "ar": (
            "➖ <b>تأكيد الخصم</b>\n\n"
            "👤 {name}\n"
            "💰 المبلغ: <b>-{amount}</b>\n"
            "💳 الرصيد: {before} → <b>{after}</b>"
        ),
        "en": (
            "➖ <b>Confirm Debit</b>\n\n"
            "👤 {name}\n"
            "💰 Amount: <b>-{amount}</b>\n"
            "💳 Balance: {before} → <b>{after}</b>"
        ),
    },

    "dwlt_success_credit": {
        "ar": "✅ <b>تم الإيداع بنجاح</b>\n\n👤 {name}\n💰 +{amount}\n💳 الرصيد الجديد: <b>{balance}</b>",
        "en": "✅ <b>Credit Successful</b>\n\n👤 {name}\n💰 +{amount}\n💳 New balance: <b>{balance}</b>",
    },
    "dwlt_success_debit": {
        "ar": "✅ <b>تم الخصم بنجاح</b>\n\n👤 {name}\n💰 -{amount}\n💳 الرصيد الجديد: <b>{balance}</b>",
        "en": "✅ <b>Debit Successful</b>\n\n👤 {name}\n💰 -{amount}\n💳 New balance: <b>{balance}</b>",
    },
    "dwlt_err_insufficient": {
        "ar": "⚠️ رصيد غير كافٍ. الحالي: {balance} — المطلوب: {amount}.",
        "en": "⚠️ Insufficient balance. Current: {balance} — Requested: {amount}.",
    },
    "dwlt_err_suspended": {
        "ar": "⚠️ هذا الموزع معلق. يجب تفعيله أولاً قبل تعديل رصيده.",
        "en": "⚠️ This distributor is suspended. Activate them before adjusting the balance.",
    },
    "dwlt_err_not_found": {
        "ar": "❌ لم يتم العثور على الموزع.",
        "en": "❌ Distributor not found.",
    },
    "dwlt_cancelled": {
        "ar": "❌ تم الإلغاء.",
        "en": "❌ Cancelled.",
    },

    "dwlt_ledger_title": {
        "ar": "📋 <b>سجل حركات {name}</b> ({count} حركة)",
        "en": "📋 <b>{name}'s Ledger</b> ({count} entries)",
    },
    "dwlt_ledger_empty": {
        "ar": "لا توجد حركات بعد.",
        "en": "No entries yet.",
    },

    "dwlt_entry_detail": {
        "ar": (
            "📄 <b>حركة #{id}</b>\n\n"
            "النوع:      {op_label}\n"
            "المبلغ:     {signed_amount}\n"
            "قبل:        {before}\n"
            "بعد:        {after}\n"
            "التاريخ:    {date}\n"
            "المرجع:     {reference}\n"
            "بواسطة:     {created_by}\n"
            "المصدر:     {source}\n"
            "ملاحظات:   {notes}"
        ),
        "en": (
            "📄 <b>Entry #{id}</b>\n\n"
            "Type:       {op_label}\n"
            "Amount:     {signed_amount}\n"
            "Before:     {before}\n"
            "After:      {after}\n"
            "Date:       {date}\n"
            "Reference:  {reference}\n"
            "By:         {created_by}\n"
            "Source:     {source}\n"
            "Notes:      {notes}"
        ),
    },

    "dwlt_op_admin_credit": {"ar": "إيداع إداري",  "en": "Admin Credit"},
    "dwlt_op_admin_debit":  {"ar": "خصم إداري",    "en": "Admin Debit"},
    "dwlt_op_unknown":      {"ar": "غير معروف",     "en": "Unknown"},
    "dwlt_no_reference":    {"ar": "—", "en": "—"},
    "dwlt_no_notes":        {"ar": "—", "en": "—"},
    "dwlt_source_telegram_admin": {"ar": "مشرف تيليغرام",  "en": "Telegram Admin"},
    "dwlt_source_recharge":       {"ar": "شحن رصيد",       "en": "Recharge"},

    # ── Admin: Preview Distributor UI (dev/testing only) ────────────────────
    "admin_btn_preview_dist": {"ar": "🧪 معاينة واجهة الموزع", "en": "🧪 Preview Distributor UI"},
    "dprev_pick_title": {
        "ar": "🧪 <b>معاينة واجهة الموزع</b>\n\nاختر موزعاً لمعاينة واجهته:",
        "en": "🧪 <b>Preview Distributor UI</b>\n\nPick a distributor to preview:",
    },
    "dprev_no_active": {
        "ar": "⚠️ لا يوجد موزعون نشطون للمعاينة.",
        "en": "⚠️ No active distributors available to preview.",
    },

    # ── Distributor self-service wallet & ledger (Phase 3B) ─────────────────
    "btn_dist_wallet":  {"ar": "💰 رصيدي",        "en": "💰 My Balance"},
    "btn_dist_history": {"ar": "📋 سجل الحركات", "en": "📋 My Ledger"},
    "dist_self_wallet_title": {
        "ar": (
            "💳 <b>محفظتي</b>\n\n"
            "رصيدك الحالي\n"
            "💰 <b>{balance}</b>"
        ),
        "en": (
            "💳 <b>My Wallet</b>\n\n"
            "Current Balance\n"
            "💰 <b>{balance}</b>"
        ),
    },
    "dist_self_btn_ledger": {"ar": "📋 سجل الحركات", "en": "📋 View Ledger"},
    "dist_self_ledger_title": {
        "ar": "📋 <b>سجل حركاتي</b> ({count} حركة)",
        "en": "📋 <b>My Ledger</b> ({count} entries)",
    },
    "dist_self_ledger_empty": {
        "ar": "لا توجد حركات بعد.",
        "en": "No entries yet.",
    },
    "dist_self_no_account": {
        "ar": "⚠️ لم يتم العثور على حسابك كموزع. يرجى التواصل مع المشرف.",
        "en": "⚠️ Your distributor account was not found. Please contact an admin.",
    },
    "dwlt_op_recharge_debit": {"ar": "خصم شحن", "en": "Recharge Debit"},

    # ── Distributor recharge (Phase 3A) ─────────────────────────────────────
    "dist_recharge_success": {
        "ar": (
            "✅ <b>تم الشحن بنجاح</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} دج</b>\n"
            "💳 محفظة الموزع: <b>{balance}</b>\n\n"
            "شكراً لاستخدامك RechargeDz Pro 🙏"
        ),
        "en": (
            "✅ <b>Recharge Successful</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "💰 <b>{amount} DZD</b>\n"
            "💳 Distributor Wallet: <b>{balance}</b>\n\n"
            "Thank you for using RechargeDz Pro 🙏"
        ),
    },
    "dist_debit_failed": {
        "ar": (
            "⚠️ <b>تنبيه: الشحن نجح، لكن حدث خطأ في المحاسبة</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "💰 <b>{amount} دج</b>\n\n"
            "تمت عملية الشحن بنجاح لدى مزود الخدمة، لكن لم يتم خصم المبلغ من محفظة الموزع.\n"
            "يرجى التواصل مع الدعم مع الإشارة إلى هذا الطلب."
        ),
        "en": (
            "⚠️ <b>Notice: Recharge succeeded but accounting error occurred</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "💰 <b>{amount} DZD</b>\n\n"
            "The recharge was sent successfully, but the distributor wallet debit failed.\n"
            "Please contact support and reference this transaction."
        ),
    },

    # ── Distributor Activy (Phase 3C) ────────────────────────────────────────
    "dist_activy_success": {
        "ar": (
            "✅ <b>تم تفعيل العرض بنجاح</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💳 محفظة الموزع: <b>{balance}</b>\n\n"
            "شكراً لاستخدامك RechargeDz Pro 🙏"
        ),
        "en": (
            "✅ <b>Offer Activated Successfully</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "🏢 {operator}\n"
            "📦 <b>{offer}</b>\n"
            "💳 Distributor Wallet: <b>{balance}</b>\n\n"
            "Thank you for using RechargeDz Pro 🙏"
        ),
    },
    "dist_activy_debit_failed": {
        "ar": (
            "⚠️ <b>تنبيه: تفعيل العرض نجح، لكن حدث خطأ في المحاسبة</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "📦 <b>{offer}</b>\n\n"
            "تم تفعيل العرض بنجاح لدى مزود الخدمة، لكن لم يتم خصم المبلغ من محفظة الموزع.\n"
            "يرجى التواصل مع الدعم مع الإشارة إلى هذا الطلب."
        ),
        "en": (
            "⚠️ <b>Notice: Offer activated but accounting error occurred</b>\n\n"
            "📞 <code>{phone}</code>\n"
            "📦 <b>{offer}</b>\n\n"
            "The offer was activated successfully, but the distributor wallet debit failed.\n"
            "Please contact support and reference this transaction."
        ),
    },

    # Operations Center (read-only live ops view)
    "ops_btn_processing": {"ar": "⏳ قيد المعالجة", "en": "⏳ Processing"},
    "ops_btn_completed":  {"ar": "✅ آخر المكتملة", "en": "✅ Last Completed"},
    "ops_list_empty":     {"ar": "لا توجد عناصر حالياً.", "en": "Nothing here right now."},

    "ops_home_title": {
        "ar": "🎛 <b>مركز العمليات</b> {pill}\n\n"
              "🔌 حالة OneClick: {reachable}\n"
              "💰 رصيد المحفظة: {wallet}\n\n"
              "📥 <b>قائمة الانتظار</b>\n"
              "🟡 قيد المعالجة: {processing_count} (بطيئة: {slow_count} — أطول من {threshold} ثانية)\n"
              "⏱ أقدم عملية قيد الانتظار: {oldest_age}\n\n"
              "📊 <b>آخر ساعة</b>\n"
              "✅ {success_1h} ناجحة / ❌ {failed_1h} فاشلة (إجمالي {completed_1h})\n"
              "⏱ متوسط زمن الإنجاز: {avg_completion}\n\n"
              "🕐 آخر تحديث: {last_checked}",
        "en": "🎛 <b>Operations Center</b> {pill}\n\n"
              "🔌 OneClick status: {reachable}\n"
              "💰 Wallet balance: {wallet}\n\n"
              "📥 <b>Queue</b>\n"
              "🟡 Processing: {processing_count} (slow: {slow_count} — waiting longer than {threshold}s)\n"
              "⏱ Oldest waiting: {oldest_age}\n\n"
              "📊 <b>Last Hour</b>\n"
              "✅ {success_1h} success / ❌ {failed_1h} failed (total {completed_1h})\n"
              "⏱ Avg completion time: {avg_completion}\n\n"
              "🕐 Last updated: {last_checked}",
    },

    "ops_processing_title": {
        "ar": "⏳ <b>المعاملات قيد المعالجة</b> ({count})\n\nاضغط على أي عنصر لعرض التفاصيل.",
        "en": "⏳ <b>Processing Transactions</b> ({count})\n\nTap any item to view details.",
    },
    "ops_completed_title": {
        "ar": "✅ <b>آخر المعاملات المكتملة</b> ({count})\n\nاضغط على أي عنصر لعرض التفاصيل.",
        "en": "✅ <b>Last Completed Transactions</b> ({count})\n\nTap any item to view details.",
    },

    # Admin FSM validation
    "admin_invalid_id":            {"ar": "❌ معرّف غير صالح. أرسل رقماً صحيحاً:", "en": "❌ Invalid ID. Send a numeric Telegram ID:"},
    "admin_user_not_found_retry":  {"ar": "❌ لم يتم العثور على المستخدم <code>{uid}</code>.", "en": "❌ User <code>{uid}</code> not found."},
    "admin_invalid_amount":        {"ar": "❌ مبلغ غير صالح. أرسل رقماً موجباً:", "en": "❌ Invalid amount. Send a positive number:"},

    # ── Errors ────────────────────────────────────────────────────────────────
    "unknown_command": {
        "ar": "❓ لم أفهم ما تقصد. استخدم القائمة أدناه.",
        "en": "❓ I didn't understand that. Use the menu below.",
    },
    "error_generic": {
        "ar": "⚠️ حدث خطأ. يرجى المحاولة لاحقاً.",
        "en": "⚠️ An error occurred. Please try again later.",
    },
    "error_unhandled_generic": {
        "ar": "⚠️ حدث خطأ ما. يرجى المحاولة مرة أخرى.",
        "en": "⚠️ Something went wrong. Please try again.",
    },
    "banned": {
        "ar": "🚫 تم حظرك من استخدام هذا البوت.",
        "en": "🚫 You have been banned from using this bot.",
    },
}


def get_text(key: str, lang: str, **kwargs: str) -> str:
    lang = lang if lang in ("ar", "en") else "ar"
    text = TEXTS.get(key, {}).get(lang, TEXTS.get(key, {}).get("ar", key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    return text


# ---------------------------------------------------------------------------
# Transaction type / status labels
# ---------------------------------------------------------------------------

TX_TYPE_LABELS: dict[str, dict[str, str]] = {
    "standard":  {"ar": "شحن عادي",    "en": "Standard"},
    "activy":    {"ar": "أكتيفي",      "en": "Activy"},
    "game":      {"ar": "ألعاب",       "en": "Game"},
    "gift_card": {"ar": "بطاقة هدية",  "en": "Gift Card"},
}

TX_STATUS_LABELS: dict[str, dict[str, str]] = {
    "success":   {"ar": "✅ ناجح",  "en": "✅ Success"},
    "failed":    {"ar": "❌ فاشل",  "en": "❌ Failed"},
    "cancelled": {"ar": "🚫 ملغى", "en": "🚫 Cancelled"},
    "pending":   {"ar": "⏳ معلق", "en": "⏳ Pending"},
}


def tx_type_label(tx_type: str, lang: str) -> str:
    return TX_TYPE_LABELS.get(tx_type, {}).get(lang, tx_type)


def tx_status_label(status: str, lang: str) -> str:
    return TX_STATUS_LABELS.get(status, {}).get(lang, status)
