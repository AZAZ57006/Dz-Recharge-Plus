"""
services.py — Business logic, operator detection, and API client.

Architecture:
  OperatorDetector  — Maps Algerian phone prefixes to operator names.
  OneClickAPI       — Production HTTP client with retry/backoff. Set
                      MOCK_MODE=false + ONECLICK_API_KEY to go live.
  RechargeService   — Standard & Activy recharges.
  GamesService      — In-game currency top-ups.
  GiftCardService   — Gift card purchases.
  WalletService     — Deposit request lifecycle.
"""

import asyncio
import logging
import random
import string
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from config import Config
from database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Product catalogues (single source of truth)
# ---------------------------------------------------------------------------

# NOTE: There is no official "Activy" product line in the OneClick v3 API.
# The "Activy" menu is instead populated live from GET /v3/mobile/plans
# (see OneClickAPI.get_fixed_plans below) — real fixedPlans for the user's
# detected operator, cached in-memory with a TTL. No local plan catalogue
# is hardcoded here anymore.

# Operator key (as used internally / by OperatorDetector) -> exact operator
# string used by OneClick in GET /v3/mobile/plans responses.
_OPERATOR_LIVE_NAME: Dict[str, str] = {
    "mobilis": "Mobilis",
    "djezzy":  "Djezzy",
    "ooredoo": "Ooredoo",
}

# How long to keep a fetched plan catalogue before re-fetching from OneClick.
# The official docs state plans are "stable and rarely change" and are
# "safe to cache", so a multi-hour TTL is appropriate.
_PLANS_CACHE_TTL = 6 * 60 * 60  # seconds (6 hours)

GAMES: Dict[str, Dict[str, Any]] = {
    "pubg": {
        "name_ar": "ببجي موبايل",
        "name_en": "PUBG Mobile",
        "emoji": "🔫",
        "currency": "UC",
        "product_id": "600b141a658904003143cb65",
    },
    "freefire": {
        "name_ar": "فري فاير",
        "name_en": "Free Fire",
        "emoji": "🔥",
        "currency": "💎",
        "product_id": "600afdf2658904003143c771",
    },
    "mobile_legends": {
        "name_ar": "موبايل ليجندز",
        "name_en": "Mobile Legends",
        "emoji": "⚔️",
        "currency": "💎",
        "product_id": "60d8380c51bed7424c20bb95",
    },
    "coc": {
        "name_ar": "كلاش أوف كلانز",
        "name_en": "Clash of Clans",
        "emoji": "⚔️",
        "currency": "💎",
        "product_id": "",
    },
    "fortnite": {
        "name_ar": "فورتنايت",
        "name_en": "Fortnite",
        "emoji": "🏆",
        "currency": "V-Bucks",
        "product_id": "",
    },
}

GIFT_CARDS: Dict[str, Dict[str, Any]] = {
    "google_play": {"name_ar": "Google Play",       "name_en": "Google Play",       "emoji": "▶️", "amounts": [500, 1000, 2000, 5000]},
    "itunes":      {"name_ar": "iTunes / App Store","name_en": "iTunes / App Store","emoji": "🍎", "amounts": [500, 1000, 2000, 5000]},
    "amazon":      {"name_ar": "Amazon",            "name_en": "Amazon",            "emoji": "📦", "amounts": [500, 1000, 2000, 5000]},
    "steam":       {"name_ar": "Steam",             "name_en": "Steam",             "emoji": "🎮", "amounts": [500, 1000, 2000, 5000]},
}

# Standard deposit amounts users can request
DEPOSIT_AMOUNTS = [500, 1000, 2000, 5000, 10000]


# ---------------------------------------------------------------------------
# Operator detection
# ---------------------------------------------------------------------------

# Keyed by the 2 digits immediately after the leading "0"
_OPERATOR_PREFIX: Dict[str, str] = {
    # Ooredoo (05xx)
    "55": "ooredoo", "56": "ooredoo", "57": "ooredoo",
    "58": "ooredoo", "59": "ooredoo",
    # Mobilis (06xx)
    "66": "mobilis", "67": "mobilis", "68": "mobilis",
    "69": "mobilis",
    # Djezzy (077x + 078x + 079x)
    "77": "djezzy", "78": "djezzy", "79": "djezzy",
}

OPERATOR_DISPLAY: Dict[str, Dict[str, str]] = {
    "mobilis":  {"ar": "موبيليس",  "en": "Mobilis",  "emoji": "🟢"},
    "djezzy":   {"ar": "جيزي",    "en": "Djezzy",   "emoji": "🔴"},
    "ooredoo":  {"ar": "أوريدو",  "en": "Ooredoo",  "emoji": "🔵"},
    "unknown":  {"ar": "غير معروف","en": "Unknown",  "emoji": "⚪"},
}


class OperatorDetector:
    @staticmethod
    def detect(phone: str) -> str:
        """Return operator key from a 10-digit Algerian phone number."""
        clean = phone.strip().lstrip("+")
        # Normalise: accept 0XXXXXXXXX or 213XXXXXXXXX
        if clean.startswith("213") and len(clean) == 12:
            clean = "0" + clean[3:]
        if len(clean) == 10 and clean.startswith("0"):
            prefix = clean[1:3]
            return _OPERATOR_PREFIX.get(prefix, "unknown")
        return "unknown"

    @staticmethod
    def to_international(phone: str) -> str:
        """0661234567 → 213661234567"""
        clean = phone.strip().lstrip("+")
        if clean.startswith("213"):
            return clean
        if clean.startswith("0"):
            return "213" + clean[1:]
        return clean

    @staticmethod
    def label(operator: str, lang: str) -> str:
        info = OPERATOR_DISPLAY.get(operator, OPERATOR_DISPLAY["unknown"])
        return f"{info['emoji']} {info[lang]}"


# ---------------------------------------------------------------------------
# Operator → API plan code mapping (for POST /v3/mobile/send)
# ---------------------------------------------------------------------------

_OPERATOR_PLAN_CODE: Dict[str, str] = {
    "mobilis": "PREPAID_MOBILIS",
    "djezzy":  "PREPAID_DJEZZY",
    "ooredoo": "PREPAID_OOREDOO",
}

# Terminal states returned by GET /v3/mobile/check-ref/{ref}
_TOPUP_FINAL_STATES = {"FULFILLED", "REFUNDED", "UNKNOWN_ERROR"}
_TOPUP_POLL_INTERVAL = 5   # seconds between status checks
_TOPUP_POLL_MAX      = 36  # max attempts (~3 minutes total)


# ---------------------------------------------------------------------------
# API error types
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Base class for OneClick API errors."""
    def __init__(self, message: str, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

class NetworkError(APIError):
    def __init__(self, cause: str) -> None:
        super().__init__(f"Network error: {cause}", code="NETWORK_ERROR", retryable=True)

class AuthError(APIError):
    def __init__(self) -> None:
        super().__init__("Invalid or missing API key", code="AUTH_ERROR", retryable=False)

class ProviderError(APIError):
    """Returned by the API as a business-logic failure (wrong phone, etc.)."""
    def __init__(self, message: str, code: str = "PROVIDER_ERROR") -> None:
        super().__init__(message, code=code, retryable=False)


# ---------------------------------------------------------------------------
# API response dataclass
# ---------------------------------------------------------------------------

@dataclass
class APIResponse:
    success: bool
    reference: str
    message: str
    code: str = ""
    raw: Optional[Dict[str, Any]] = None
    suggested_offers: Optional[List[Dict[str, Any]]] = None

    def to_log_str(self) -> str:
        return f"success={self.success} ref={self.reference} code={self.code}"


# ---------------------------------------------------------------------------
# OneClick API client
# ---------------------------------------------------------------------------

class OneClickAPI:
    """
    Production-ready async HTTP client for the OneClick Flexy API v3.

    Real API base: https://api.oneclickdz.com/v3
    Auth header:   X-Access-Token: <key>
    Response shape: {"success": bool, "data": {...}, "error": {...}}

    When MOCK_MODE is True all calls are simulated locally.
    Set MOCK_MODE=false and supply ONECLICK_API_KEY to switch to live mode.

    Retry strategy: exponential backoff on network errors and 5xx responses.
    Retries: up to config.API_MAX_RETRIES attempts.
    """

    # HTTP status codes that warrant a retry
    _RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._plans_cache: Optional[Dict[str, Any]] = None
        self._plans_cache_ts: float = 0.0

    def _session_headers(self) -> Dict[str, str]:
        return {
            "X-Access-Token": self._config.ONECLICK_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Client": "RechargeDzBot/2.0",
        }

    def _url(self, path: str) -> str:
        """Build an absolute URL from the configured base and the given path."""
        base = self._config.ONECLICK_API_URL.rstrip("/")

        # Allow API calls to explicitly target another API version,
        # e.g. /v2/topup/checkStatus/... while the normal base is /v3.
        if path.startswith("/v2/"):
            if base.endswith("/v3"):
                base = base[:-3].rstrip("/")
            return base + path

        return base + path

    def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._session_headers(),
                timeout=aiohttp.ClientTimeout(total=self._config.API_TIMEOUT),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _parse_response(self, raw: Dict[str, Any]) -> APIResponse:
        """
        Parse the standard OneClick v3 response shape:
          {"success": bool, "data": {...}, "error": {"code": ..., "message": ...}}
        """
        success = bool(raw.get("success", False))
        if success:
            data = raw.get("data", {})
            ref = str(
                data.get("topupId") or data.get("topupRef") or
                data.get("orderId") or data.get("_id") or ""
            )
            return APIResponse(success=True, reference=ref, message="OK", raw=raw)
        else:
            error = raw.get("error", {})
            return APIResponse(
                success=False,
                reference="",
                message=str(error.get("message", "")),
                code=str(error.get("code", "")),
                raw=raw,
            )

    async def _post(self, path: str, payload: Dict[str, Any]) -> APIResponse:
        """POST with JSON body, with exponential-backoff retry."""
        if not self._config.ONECLICK_API_KEY:
            logger.error("ONECLICK_API_KEY is not set — cannot make live API calls.")
            raise AuthError()

        last_exc: Exception = Exception("Unknown error")

        for attempt in range(self._config.API_MAX_RETRIES):
            wait = 2 ** attempt
            try:
                async with self._get_session().post(self._url(path), json=payload) as resp:
                    if resp.status == 401:
                        raise AuthError()

                    raw: Dict[str, Any] = await resp.json(content_type=None)
                    logger.debug("API POST %s → %s | %s", path, resp.status, raw)

                    if resp.status in self._RETRYABLE_STATUSES:
                        logger.warning(
                            "API transient error %s on attempt %d/%d — retrying in %ds",
                            resp.status, attempt + 1, self._config.API_MAX_RETRIES, wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    return self._parse_response(raw)

            except AuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                logger.warning(
                    "Network error on attempt %d/%d: %s — retrying in %ds",
                    attempt + 1, self._config.API_MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)

        raise NetworkError(str(last_exc))

    async def _get(self, path: str) -> APIResponse:
        """GET request, with exponential-backoff retry."""
        if not self._config.ONECLICK_API_KEY:
            raise AuthError()

        last_exc: Exception = Exception("Unknown error")

        for attempt in range(self._config.API_MAX_RETRIES):
            wait = 2 ** attempt
            try:
                async with self._get_session().get(self._url(path)) as resp:
                    if resp.status == 401:
                        raise AuthError()

                    raw: Dict[str, Any] = await resp.json(content_type=None)
                    logger.debug("API GET %s → %s | %s", path, resp.status, raw)

                    if resp.status in self._RETRYABLE_STATUSES:
                        await asyncio.sleep(wait)
                        continue

                    return self._parse_response(raw)

            except AuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                await asyncio.sleep(wait)

        raise NetworkError(str(last_exc))

    async def _poll_topup_status(self, topup_id: str) -> APIResponse:
        """
        Poll OneClick v2 status endpoint using the topup ID returned by
        POST /v3/mobile/send.

        The v2 status response may contain:
          - status
          - refund_message
          - suggested_offers

        suggested_offers is especially important for GETMENU_* requests,
        where OneClick returns the offers compatible with the phone number.
        """
        for attempt in range(_TOPUP_POLL_MAX):
            try:
                resp = await self._get(
                    f"/v2/topup/checkStatus/ID/{topup_id}"
                )
            except (NetworkError, APIError):
                continue

            if not resp.raw:
                continue

            topup = resp.raw.get("topup", {})
            status = str(topup.get("status", "")).upper()

            logger.debug(
                "Polling topup_id=%s attempt=%d status=%s",
                topup_id,
                attempt + 1,
                status,
            )

            if status in _TOPUP_FINAL_STATES:
                suggested_offers = topup.get("suggested_offers") or []
                refund_msg = str(topup.get("refund_message", ""))

                success = status == "FULFILLED"

                logger.info(
                    "OneClick status — topup_id=%s attempt=%d status=%s "
                    "suggested_offers=%d",
                    topup_id,
                    attempt + 1,
                    status,
                    len(suggested_offers),
                )

                return APIResponse(
                    success=success,
                    reference=str(topup.get("_id", topup_id)),
                    message=refund_msg if not success else "FULFILLED",
                    code=status,
                    raw=resp.raw,
                    suggested_offers=suggested_offers,
                )

            # If the status is not terminal yet, wait before the next check.
            if attempt < _TOPUP_POLL_MAX - 1:
                await asyncio.sleep(_TOPUP_POLL_INTERVAL)

        logger.error(
            "Polling timed out for topup_id=%s after %d attempts",
            topup_id,
            _TOPUP_POLL_MAX,
        )

        return APIResponse(
            success=False,
            reference=topup_id,
            message="Top-up status unknown after polling timeout",
            code="POLL_TIMEOUT",
            raw=None,
            suggested_offers=[],
        )

    # ── mock helpers ─────────────────────────────────────────────────────────

    @staticmethod
    async def _mock_success(extra: Optional[Dict[str, Any]] = None) -> APIResponse:
        await asyncio.sleep(0.4)
        ref = "MOCK-" + uuid.uuid4().hex[:8].upper()
        data = {"reference": ref, **(extra or {})}
        return APIResponse(success=True, reference=ref, message="Mock success", raw=data)

    # ── public endpoints ─────────────────────────────────────────────────────

    async def recharge_standard(
        self, phone: str, amount: int, operator: str
    ) -> APIResponse:
        """
        Send a standard prepaid mobile top-up via POST /v3/mobile/send.

        Uses the operator → plan_code mapping. Phone stays in local format
        (0XXXXXXXXX) as required by the real API.
        Polls for final status before returning.
        """
        if self._config.MOCK_MODE:
            return await self._mock_success()

        plan_code = _OPERATOR_PLAN_CODE.get(operator, f"PREPAID_{operator.upper()}")
        ref = f"bot-std-{uuid.uuid4().hex[:12]}"

        logger.info(
            "Recharge completion flow: POST /mobile/send — phone=%s amount=%s operator=%s "
            "plan_code=%s ref=%s",
            phone, amount, operator, plan_code, ref,
        )
        send_resp = await self._post("/mobile/send", {
            "plan_code": plan_code,
            "MSSIDN": phone,
            "amount": amount,
            "ref": ref,
        })
        logger.info(
            "Recharge completion flow: /mobile/send response — ref=%s success=%s code=%s message=%s",
            ref, send_resp.success, send_resp.code, send_resp.message,
        )

        if not send_resp.success:
            return send_resp

        topup_id = ""
        if send_resp.raw:
            topup_id = str(
                send_resp.raw.get("data", {}).get("topupId", "")
            )

        if not topup_id:
            logger.error(
                "Standard recharge: OneClick did not return topupId — ref=%s",
                ref,
            )
            return APIResponse(
                success=False,
                reference=ref,
                message="OneClick did not return topupId",
                code="MISSING_TOPUP_ID",
                raw=send_resp.raw,
                suggested_offers=[],
            )

        logger.info(
            "Recharge completion flow: starting status poll — topup_id=%s ref=%s",
            topup_id,
            ref,
        )
        poll_result = await self._poll_topup_status(topup_id)
        logger.info(
            "Recharge completion flow: poll finished — topup_id=%s ref=%s "
            "success=%s code=%s message=%s",
            topup_id,
            ref,
            poll_result.success,
            poll_result.code,
            poll_result.message,
        )
        return poll_result

    async def recharge_activy(self, phone: str, offer_code: str, amount: float) -> APIResponse:
        """
        Send an Activy-menu top-up via POST /v3/mobile/send.

        The offer_code is a real `code` value taken directly from the live
        GET /v3/mobile/plans catalogue (see get_fixed_plans) and is used
        as-is as the plan_code. `amount` is the plan's live price (also
        from the catalogue) and, like recharge_standard, is required by
        OneClick — omitting it causes an ERR_VALIDATION rejection. Polls
        for final status before returning.
        """
        if self._config.MOCK_MODE:
            return await self._mock_success()

        ref = f"bot-act-{uuid.uuid4().hex[:12]}"

        logger.info(
            "Recharge completion flow: POST /mobile/send (activy) — phone=%s amount=%s "
            "plan_code=%s ref=%s",
            phone, amount, offer_code, ref,
        )
        send_resp = await self._post("/mobile/send", {
            "plan_code": offer_code,
            "MSSIDN": phone,
            "amount": amount,
            "ref": ref,
        })
        logger.info(
            "Recharge completion flow: /mobile/send (activy) response — ref=%s success=%s "
            "code=%s message=%s",
            ref, send_resp.success, send_resp.code, send_resp.message,
        )

        if not send_resp.success:
            return send_resp

        topup_id = ""
        if send_resp.raw:
            topup_id = str(
                send_resp.raw.get("data", {}).get("topupId", "")
            )

        if not topup_id:
            logger.error(
                "Activy recharge: OneClick did not return topupId — ref=%s",
                ref,
            )
            return APIResponse(
                success=False,
                reference=ref,
                message="OneClick did not return topupId",
                code="MISSING_TOPUP_ID",
                raw=send_resp.raw,
                suggested_offers=[],
            )

        logger.info(
            "Recharge completion flow: starting status poll — topup_id=%s ref=%s",
            topup_id,
            ref,
        )

        poll_result = await self._poll_topup_status(topup_id)

        logger.info(
            "Recharge completion flow: poll finished (activy) — topup_id=%s ref=%s "
            "success=%s code=%s message=%s suggested_offers=%d",
            topup_id,
            ref,
            poll_result.success,
            poll_result.code,
            poll_result.message,
            len(poll_result.suggested_offers or []),
        )
        return poll_result

    async def recharge_game(
        self, game_id: str, amount: int, player_id: str
    ) -> APIResponse:
        """
        Game top-ups are not offered by the OneClick v3 API.
        Remains in mock mode only.
        """
        return await self._mock_success()

    async def validate_api_key(self) -> Dict[str, Any]:
        """
        Call GET /v3/validate to check the API key and return account info.

        Returns a dict with keys: valid (bool), username, key_type, key_enabled,
        scope, error_message.
        Always makes a real HTTP call regardless of MOCK_MODE.
        """
        if not self._config.ONECLICK_API_KEY:
            return {"valid": False, "error_message": "ONECLICK_API_KEY is not set"}
        try:
            resp = await self._get("/validate")
        except AuthError:
            return {"valid": False, "error_message": "Invalid or missing API key (401)"}
        except NetworkError as exc:
            return {"valid": False, "error_message": str(exc)}

        if resp.success and resp.raw:
            data = resp.raw.get("data", {})
            api_key = data.get("apiKey", {})
            return {
                "valid": True,
                "username": str(data.get("username", "")),
                "key_type": str(api_key.get("type", "")),
                "key_enabled": bool(api_key.get("isEnabled", False)),
                "scope": str(api_key.get("scope", "")),
                "error_message": "",
            }
        error = (resp.raw or {}).get("error", {})
        return {
            "valid": False,
            "error_message": str(error.get("message", "Unknown error")),
        }

    async def get_mobile_plans(self) -> Dict[str, Any]:
        """
        Call GET /v3/mobile/plans and return the live plan catalogue.

        Returns a dict with keys: success (bool), dynamicPlans (list),
        fixedPlans (list), error_message (str).
        Always makes a real HTTP call regardless of MOCK_MODE, since this
        catalogue must reflect the real account — no local plan data exists.
        """
        if not self._config.ONECLICK_API_KEY:
            return {
                "success": False, "dynamicPlans": [], "fixedPlans": [],
                "error_message": "ONECLICK_API_KEY is not set",
            }
        try:
            resp = await self._get("/mobile/plans")
        except (AuthError, NetworkError) as exc:
            return {
                "success": False, "dynamicPlans": [], "fixedPlans": [],
                "error_message": str(exc),
            }

        if resp.success and resp.raw:
            data = resp.raw.get("data", {})
            return {
                "success": True,
                "dynamicPlans": data.get("dynamicPlans", []),
                "fixedPlans": data.get("fixedPlans", []),
                "error_message": "",
            }
        return {
            "success": False, "dynamicPlans": [], "fixedPlans": [],
            "error_message": resp.message,
        }

    async def get_fixed_plans(
        self, operator: str, force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Return the live, enabled fixedPlans for the given internal operator
        key ("mobilis" | "djezzy" | "ooredoo"), using an in-memory cache.

        The full catalogue (GET /v3/mobile/plans) is fetched at most once
        per _PLANS_CACHE_TTL seconds and reused across users/operators.

        Returns a dict with keys: success (bool), plans (list of the raw
        fixedPlan dicts as returned by OneClick, unmodified), error_message.
        """
        live_operator = _OPERATOR_LIVE_NAME.get(operator)
        if not live_operator:
            return {"success": False, "plans": [], "error_message": "Unknown operator"}

        now = time.monotonic()
        cache_is_fresh = (
            self._plans_cache is not None
            and (now - self._plans_cache_ts) < _PLANS_CACHE_TTL
        )
        if force_refresh or not cache_is_fresh:
            result = await self.get_mobile_plans()
            if not result["success"]:
                # Serve stale cache rather than nothing, if we have one.
                if self._plans_cache is not None:
                    logger.warning(
                        "Live plan refresh failed (%s) — serving cached catalogue",
                        result["error_message"],
                    )
                else:
                    return {"success": False, "plans": [], "error_message": result["error_message"]}
            else:
                self._plans_cache = result
                self._plans_cache_ts = now

        catalogue = self._plans_cache or {}
        fixed_plans = catalogue.get("fixedPlans", [])
        plans = [
            p for p in fixed_plans
            if p.get("operator") == live_operator
            and p.get("isEnabled") is True
            and p.get("amount", 0) > 0
        ]
        return {"success": True, "plans": plans, "error_message": ""}

    async def get_activy_offers(
        self, phone: str, operator: str
    ) -> Dict[str, Any]:
        """
        Fetch Activy offers specifically available for the given phone number.

        OneClick GETMENU works by sending a zero-value top-up request using
        GETMENU_<Operator>. The resulting topupId is then checked through the
        v2 status endpoint, whose suggested_offers contains the offers
        compatible with the phone number.
        """
        live_operator = _OPERATOR_LIVE_NAME.get(operator)
        if not live_operator:
            return {
                "success": False,
                "plans": [],
                "error_message": "Unknown operator",
            }

        menu_code = f"GETMENU_{live_operator}"

        logger.info(
            "Activy GETMENU: requesting phone-specific offers — phone=%s "
            "operator=%s plan_code=%s",
            phone,
            operator,
            menu_code,
        )

        result = await self.recharge_activy(phone, menu_code, 0)

        if result.suggested_offers:
            plans = [
                {
                    "code": str(offer.get("plan_code", "")),
                    "name": str(offer.get("typename", offer.get("plan_code", ""))),
                    "amount": float(offer.get("amount", 0)),
                    "operator": live_operator,
                    "isEnabled": True,
                }
                for offer in result.suggested_offers
                if offer.get("plan_code") and float(offer.get("amount", 0)) > 0
            ]

            logger.info(
                "Activy GETMENU: received %d offers for phone=%s",
                len(plans),
                phone,
            )

            return {
                "success": True,
                "plans": plans,
                "error_message": "",
            }

        return {
            "success": False,
            "plans": [],
            "error_message": result.message or "No Activy offers returned",
        }

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Call GET /v3/account/balance and return the account balance.

        Returns a dict with keys: success (bool), balance (float), error_message.
        Always makes a real HTTP call regardless of MOCK_MODE.
        """
        if not self._config.ONECLICK_API_KEY:
            return {"success": False, "balance": 0.0, "error_message": "ONECLICK_API_KEY is not set"}
        try:
            resp = await self._get("/account/balance")
        except (AuthError, NetworkError) as exc:
            return {"success": False, "balance": 0.0, "error_message": str(exc)}

        if resp.success and resp.raw:
            data = resp.raw.get("data", {})
            return {
                "success": True,
                "balance": float(data.get("balance", 0.0)),
                "error_message": "",
            }
        return {"success": False, "balance": 0.0, "error_message": resp.message}

    async def get_gift_card_catalog(self) -> APIResponse:
        """Read the live OneClick gift-card catalog. No purchase."""
        return await self._get("/gift-cards/catalog")

    async def check_gift_card_product(self, product_id: str) -> APIResponse:
        """Read live pricing and stock for one gift-card product. No purchase."""
        return await self._get(f"/gift-cards/checkProduct/{product_id}")

    async def place_gift_card_order(
        self,
        product_id: str,
        type_id: str,
        quantity: int,
    ) -> APIResponse:
        """
        Place a REAL OneClick gift-card order.

        IMPORTANT: calling this method creates a real provider order.
        Caller must validate stock, price and wallet balance first.
        """
        if quantity < 1:
            return APIResponse(
                success=False,
                reference="",
                message="quantity must be >= 1",
                code="INVALID_QUANTITY",
            )

        return await self._post("/gift-cards/placeOrder", {
            "productId": product_id,
            "typeId": type_id,
            "quantity": quantity,
        })

    async def check_gift_card_order(self, order_id: str) -> APIResponse:
        """Read the status of an existing gift-card order."""
        return await self._get(f"/gift-cards/checkOrder/{order_id}")

    async def purchase_gift_card(self, card_type: str, amount: int) -> APIResponse:
        """
        Gift card purchases via the OneClick v3 API require fetching the product
        catalog first (GET /gift-cards/catalog → POST /gift-cards/checkProduct)
        to obtain productId and typeId. This flow is not yet integrated.
        Remains in mock mode only.
        """
        if self._config.MOCK_MODE:
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
            return await self._mock_success({"card_code": code})
        # Live mode: catalog-based flow not implemented — return mock result
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
        return await self._mock_success({"card_code": code})


# ---------------------------------------------------------------------------
# Recharge service
# ---------------------------------------------------------------------------

class RechargeService:
    def __init__(self, db: Database, api: OneClickAPI) -> None:
        self._db = db
        self._api = api

    async def process_standard(
        self,
        telegram_id: int,
        db_user_id: int,
        phone: str,
        amount: int,
        operator: Optional[str] = None,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        balance = await self._db.get_balance(telegram_id)
        if not is_admin and balance < amount:
            return {"success": False, "reason": "insufficient_balance",
                    "balance": balance, "required": amount}

        # `operator` is only passed in when the user manually picked it
        # (auto-detection returned "unknown"). Otherwise, detect as before —
        # this keeps the normal standard-recharge path unchanged.
        if not operator:
            operator = OperatorDetector.detect(phone)
        tx_id = await self._db.create_transaction(
            user_id=db_user_id, tx_type="standard", amount=amount,
            description=f"Recharge {phone}", phone=phone,
            status="pending", operator=operator,
        )

        try:
            result = await self._api.recharge_standard(phone, amount, operator)
        except (NetworkError, APIError) as exc:
            logger.error("Standard recharge API error: %s", exc)
            await self._db.update_transaction(tx_id, "failed", str(exc))
            await self._db.log(
                "recharge_api_error", f"phone={phone} error={exc}",
                db_user_id, level="ERROR",
            )
            return {"success": False, "reason": "api_error"}

        if result.success:
            logger.info(
                "Recharge completion flow: final status update — tx_id=%s phone=%s amount=%s "
                "operator=%s ref=%s status=success",
                tx_id, phone, amount, operator, result.reference,
            )
            if not is_admin:
                await self._db.adjust_balance(telegram_id, -amount)
            await self._db.update_transaction(tx_id, "success", str(result.raw), result.reference)
            await self._db.log(
                "standard_recharge",
                f"phone={phone} amount={amount} operator={operator} ref={result.reference}",
                db_user_id,
            )
            logger.info(
                "Recharge completion flow: transaction persisted as success — tx_id=%s phone=%s "
                "ref=%s — handing off to caller for Telegram delivery",
                tx_id, phone, result.reference,
            )
            return {"success": True, "phone": phone, "amount": amount,
                    "operator": operator, "reference": result.reference}
        else:
            logger.info(
                "Recharge completion flow: final status update — tx_id=%s phone=%s amount=%s "
                "operator=%s code=%s status=failed",
                tx_id, phone, amount, operator, result.code,
            )
            await self._db.update_transaction(tx_id, "failed", str(result.raw))
            await self._db.log(
                "standard_recharge_failed",
                f"phone={phone} amount={amount} code={result.code}",
                db_user_id, level="WARNING",
            )
            return {"success": False, "reason": "provider_error", "message": result.message}

    async def process_activy(
        self,
        telegram_id: int,
        db_user_id: int,
        phone: str,
        plan_code: str,
        plan_name: str,
        amount: float,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Activate a live OneClick fixed plan (plan_code/plan_name/amount are
        taken directly from GET /v3/mobile/plans — see OneClickAPI.get_fixed_plans).
        """
        price = float(amount)
        balance = await self._db.get_balance(telegram_id)
        if not is_admin and balance < price:
            return {"success": False, "reason": "insufficient_balance",
                    "balance": balance, "required": price}

        # Created directly with status "processing" (not "pending") — by the
        # time this is called, the confirm callback's idempotency token has
        # already been claimed, so this transaction is guaranteed to run the
        # OneClick call exactly once.
        tx_id = await self._db.create_transaction(
            user_id=db_user_id, tx_type="activy", amount=price,
            description=f"Activy {plan_name} → {phone}",
            phone=phone, status="processing",
        )

        try:
            result = await self._api.recharge_activy(phone, plan_code, price)
        except (NetworkError, APIError) as exc:
            logger.error("Activy recharge API error: %s", exc)
            await self._db.update_transaction(tx_id, "failed", str(exc))
            return {"success": False, "reason": "api_error"}

        if result.success:
            if not is_admin:
                await self._db.adjust_balance(telegram_id, -price)
            await self._db.update_transaction(tx_id, "success", str(result.raw), result.reference)
            await self._db.log(
                "activy_recharge",
                f"phone={phone} offer={plan_code} ref={result.reference}",
                db_user_id,
            )
            return {"success": True, "phone": phone,
                    "offer": {"name_en": plan_name, "name_ar": plan_name,
                              "price": price, "code": plan_code},
                    "reference": result.reference}
        else:
            await self._db.update_transaction(tx_id, "failed", str(result.raw))
            return {"success": False, "reason": "provider_error", "message": result.message}


# ---------------------------------------------------------------------------
# Games service
# ---------------------------------------------------------------------------

class GamesService:
    def __init__(self, db: Database, api: OneClickAPI) -> None:
        self._db = db
        self._api = api

    def get_games(self) -> Dict[str, Dict[str, Any]]:
        return GAMES

    def get_game(self, game_id: str) -> Optional[Dict[str, Any]]:
        return GAMES.get(game_id)

    async def get_live_packages(
        self,
        game_id: str,
    ) -> Dict[str, Any]:
        """
        Read the live OneClick packages for a game.

        No order is placed here. This only reads current type IDs,
        prices and stock.
        """
        game = GAMES.get(game_id)
        if not game:
            return {
                "success": False,
                "reason": "invalid_game",
                "packages": [],
            }

        product_id = game.get("product_id")
        if not product_id:
            return {
                "success": False,
                "reason": "missing_product_id",
                "packages": [],
            }

        try:
            result = await self._api.check_gift_card_product(product_id)
        except (NetworkError, APIError) as exc:
            logger.error(
                "Game package lookup API error — game=%s product_id=%s error=%s",
                game_id,
                product_id,
                exc,
            )
            return {
                "success": False,
                "reason": "api_error",
                "packages": [],
            }

        if not result.success or not result.raw:
            logger.warning(
                "Game package lookup failed — game=%s product_id=%s code=%s message=%s",
                game_id,
                product_id,
                result.code,
                result.message,
            )
            return {
                "success": False,
                "reason": "provider_error",
                "message": result.message,
                "packages": [],
            }

        data = result.raw.get("data", {})
        types = data.get("types", [])

        packages: List[Dict[str, Any]] = []

        for item in types:
            type_id = str(item.get("id", ""))
            name = str(item.get("name", "")).strip()

            try:
                price = float(item.get("price", 0))
                quantity = int(item.get("quantity", 0))
            except (TypeError, ValueError):
                continue

            # Never show invalid, free/zero-price or out-of-stock packages.
            if not type_id or not name or price <= 0 or quantity <= 0:
                continue

            packages.append({
                "type_id": type_id,
                "amount": name,
                "price": price,
                "quantity": quantity,
            })

        return {
            "success": True,
            "game": game,
            "product_id": product_id,
            "product_title": data.get(
                "productTitle",
                game["name_en"],
            ),
            "packages": packages,
        }

    async def process(
        self,
        telegram_id: int,
        db_user_id: int,
        game_id: str,
        type_id: str,
        player_id: str = "",
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Place a real OneClick digital-product order for a game.

        Flow:
          checkProduct -> validate package -> balance check
          -> placeOrder -> checkOrder polling -> persist success/failure.

        `player_id` is kept for callback compatibility. The current OneClick
        gift-card products are fulfilled as digital products and do not use it.
        """
        game = GAMES.get(game_id)
        if not game:
            return {"success": False, "reason": "invalid_game"}

        # Re-read the live catalogue at confirmation time so the selected
        # package is validated against the current provider price and stock.
        live = await self.get_live_packages(game_id)
        if not live.get("success"):
            return {
                "success": False,
                "reason": live.get("reason", "provider_error"),
                "message": live.get("message", ""),
            }

        packages = live.get("packages", [])

        selected_type_id = str(type_id or "").strip()
        if not selected_type_id:
            return {"success": False, "reason": "invalid_package"}

        pkg = next(
            (
                p
                for p in packages
                if str(p.get("type_id", "")).strip() == selected_type_id
            ),
            None,
        )

        if pkg is None:
            return {"success": False, "reason": "invalid_package"}

        product_id = str(live.get("product_id") or game.get("product_id") or "")
        type_id = str(pkg.get("type_id") or "")
        price = float(pkg.get("price", 0))
        amount = str(pkg.get("amount", ""))
        quantity = int(pkg.get("quantity", 0))

        if not product_id or not type_id:
            return {"success": False, "reason": "invalid_product"}

        if price <= 0:
            return {"success": False, "reason": "invalid_price"}

        if quantity <= 0:
            return {"success": False, "reason": "out_of_stock"}

        balance = await self._db.get_balance(telegram_id)
        if not is_admin and balance < price:
            return {
                "success": False,
                "reason": "insufficient_balance",
                "balance": balance,
                "required": price,
            }

        tx_id = await self._db.create_transaction(
            user_id=db_user_id,
            tx_type="game",
            amount=price,
            description=f"{game['name_en']} {amount}",
            status="processing",
        )

        try:
            order_result = await self._api.place_gift_card_order(
                product_id=product_id,
                type_id=type_id,
                quantity=1,
            )
        except (NetworkError, APIError) as exc:
            logger.error(
                "Game order API error — game=%s product_id=%s type_id=%s error=%s",
                game_id,
                product_id,
                type_id,
                exc,
            )
            await self._db.update_transaction(tx_id, "failed", str(exc))
            return {"success": False, "reason": "api_error"}

        if not order_result.success:
            logger.warning(
                "Game order rejected — game=%s product_id=%s type_id=%s "
                "code=%s message=%s",
                game_id,
                product_id,
                type_id,
                order_result.code,
                order_result.message,
            )
            await self._db.update_transaction(
                tx_id,
                "failed",
                str(order_result.raw),
            )
            return {
                "success": False,
                "reason": "provider_error",
                "message": order_result.message,
            }

        order_id = str(order_result.reference or "")

        if not order_id and order_result.raw:
            order_id = str(
                order_result.raw.get("data", {}).get("orderId", "")
            )

        if not order_id:
            logger.error(
                "Game order created without orderId — game=%s product_id=%s "
                "type_id=%s",
                game_id,
                product_id,
                type_id,
            )
            await self._db.update_transaction(
                tx_id,
                "failed",
                "Missing orderId from OneClick",
            )
            return {
                "success": False,
                "reason": "missing_order_id",
            }

        logger.info(
            "Game order created — tx_id=%s game=%s order_id=%s "
            "product_id=%s type_id=%s price=%s",
            tx_id,
            game_id,
            order_id,
            product_id,
            type_id,
            price,
        )

        # Poll the provider order until it reaches a terminal state.
        final_result = None

        for attempt in range(15):
            try:
                check = await self._api.check_gift_card_order(order_id)
            except (NetworkError, APIError) as exc:
                logger.error(
                    "Game order status API error — order_id=%s attempt=%s error=%s",
                    order_id,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(2)
                continue

            raw_data = (check.raw or {}).get("data", {}) if check.raw else {}
            status = str(
                raw_data.get("status")
                or raw_data.get("orderStatus")
                or ""
            ).upper()

            logger.info(
                "Game order status — tx_id=%s order_id=%s attempt=%s "
                "status=%s success=%s",
                tx_id,
                order_id,
                attempt + 1,
                status,
                check.success,
            )

            if status in {"FULFILLED", "PARTIALLY_FILLED", "REFUNDED"}:
                final_result = check
                break

            if not check.success:
                final_result = check
                break

            await asyncio.sleep(2)

        if final_result is None:
            await self._db.update_transaction(
                tx_id,
                "failed",
                f"Order status timeout: {order_id}",
            )
            return {
                "success": False,
                "reason": "order_timeout",
                "order_id": order_id,
            }

        final_data = (
            (final_result.raw or {}).get("data", {})
            if final_result.raw
            else {}
        )

        final_status = str(
            final_data.get("status")
            or final_data.get("orderStatus")
            or ""
        ).upper()

        if final_status != "FULFILLED":
            await self._db.update_transaction(
                tx_id,
                "failed",
                str(final_result.raw),
                order_id,
            )
            return {
                "success": False,
                "reason": "order_failed",
                "order_id": order_id,
                "status": final_status,
                "message": final_result.message,
            }

        # Provider order is fulfilled. Only now debit a normal user's balance.
        # Admin purchases are charged against the OneClick provider balance.
        if not is_admin:
            await self._db.adjust_balance(telegram_id, -price)

        await self._db.update_transaction(
            tx_id,
            "success",
            str(final_result.raw),
            order_id,
        )

        await self._db.log(
            "game_recharge",
            f"game={game_id} amount={amount} price={price} "
            f"product_id={product_id} type_id={type_id} order_id={order_id}",
            db_user_id,
        )

        cards = final_data.get("cards", [])
        card = cards[0] if isinstance(cards, list) and cards else {}

        return {
            "success": True,
            "game": game,
            "amount": amount,
            "price": price,
            "reference": order_id,
            "order_id": order_id,
            "card": card,
        }


# ---------------------------------------------------------------------------
# Gift card service
# ---------------------------------------------------------------------------

class GiftCardService:
    def __init__(self, db: Database, api: OneClickAPI) -> None:
        self._db = db
        self._api = api

    def get_cards(self) -> Dict[str, Dict[str, Any]]:
        return GIFT_CARDS

    def get_card(self, card_type: str) -> Optional[Dict[str, Any]]:
        return GIFT_CARDS.get(card_type)

    async def process(
        self,
        telegram_id: int,
        db_user_id: int,
        card_type: str,
        amount: int,
    ) -> Dict[str, Any]:
        card = GIFT_CARDS.get(card_type)
        if not card or amount not in card["amounts"]:
            return {"success": False, "reason": "invalid_card"}

        balance = await self._db.get_balance(telegram_id)
        if balance < amount:
            return {"success": False, "reason": "insufficient_balance",
                    "balance": balance, "required": amount}

        tx_id = await self._db.create_transaction(
            user_id=db_user_id, tx_type="gift_card", amount=float(amount),
            description=f"{card['name_en']} {amount} DZD",
            status="pending",
        )

        try:
            result = await self._api.purchase_gift_card(card_type, amount)
        except (NetworkError, APIError) as exc:
            logger.error("Gift card API error: %s", exc)
            await self._db.update_transaction(tx_id, "failed", str(exc))
            return {"success": False, "reason": "api_error"}

        if result.success:
            await self._db.adjust_balance(telegram_id, -amount)
            card_code = result.raw.get("card_code", "XXXX-XXXX-XXXX-XXXX") if result.raw else "XXXX"
            await self._db.update_transaction(tx_id, "success", str(result.raw), result.reference)
            await self._db.log(
                "gift_card",
                f"type={card_type} amount={amount} ref={result.reference}",
                db_user_id,
            )
            return {"success": True, "card": card, "amount": amount, "code": card_code}
        else:
            await self._db.update_transaction(tx_id, "failed", str(result.raw))
            return {"success": False, "reason": "provider_error"}


# ---------------------------------------------------------------------------
# Wallet / deposit service
# ---------------------------------------------------------------------------

class WalletService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def request_deposit(
        self, telegram_id: int, db_user_id: int, amount: float
    ) -> Dict[str, Any]:
        request_id = await self._db.create_deposit_request(db_user_id, amount)
        await self._db.log(
            "deposit_requested",
            f"amount={amount} request_id={request_id}",
            db_user_id,
        )
        return {"success": True, "request_id": request_id, "amount": amount}

    async def approve_deposit(
        self, request_id: int, admin_telegram_id: int
    ) -> Dict[str, Any]:
        req = await self._db.get_deposit_request(request_id)
        if not req:
            return {"success": False, "reason": "not_found"}
        if req["status"] != "pending":
            return {"success": False, "reason": "already_resolved", "status": req["status"]}

        new_balance = await self._db.adjust_balance(req["telegram_id"], req["amount"])
        await self._db.resolve_deposit(request_id, "approved")
        await self._db.log(
            "deposit_approved",
            f"request_id={request_id} amount={req['amount']} admin={admin_telegram_id}",
            req.get("user_id"),
        )
        return {
            "success": True, "user_telegram_id": req["telegram_id"],
            "amount": req["amount"], "new_balance": new_balance,
            "user_name": req.get("full_name", "User"),
        }

    async def reject_deposit(
        self, request_id: int, admin_telegram_id: int, note: str = ""
    ) -> Dict[str, Any]:
        req = await self._db.get_deposit_request(request_id)
        if not req:
            return {"success": False, "reason": "not_found"}
        if req["status"] != "pending":
            return {"success": False, "reason": "already_resolved", "status": req["status"]}

        await self._db.resolve_deposit(request_id, "rejected", note)
        await self._db.log(
            "deposit_rejected",
            f"request_id={request_id} admin={admin_telegram_id} note={note}",
            req.get("user_id"),
        )
        return {
            "success": True, "user_telegram_id": req["telegram_id"],
            "amount": req["amount"], "user_name": req.get("full_name", "User"),
        }

    async def get_pending(self) -> List[Dict[str, Any]]:
        return await self._db.get_pending_deposits()
