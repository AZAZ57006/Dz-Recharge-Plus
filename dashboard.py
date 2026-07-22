"""
Admin Dashboard — strictly read-only reporting/diagnostics layer.

This module never writes to the database (aside from its own in-memory
cache) and never touches wallet balance, recharge processing, or the
idempotency/transaction write paths. It only:
  - reads existing OneClick methods that already exist for other features
    (get_account_balance, get_fixed_plans — both already cached by
    OneClickAPI itself), timing them from the *outside* so no OneClick
    internals are modified, and
  - reads aggregate/report methods on Database (see the "Admin Dashboard"
    section of database.py), all of which are pure SELECTs.

Caching: OneClick-derived facts (wallet balance, reachability, offer
counts, response time) are cached in-memory for `DASHBOARD_CACHE_TTL`
seconds so opening/browsing the dashboard never triggers extra OneClick
calls beyond what the existing 6h plans cache would anyway. DB-derived
facts (statistics, last activity) are cheap indexed local queries and are
always read fresh, so "Last Activity" naturally only changes when the
dashboard is opened/refreshed (there is no push/live update).
"""

import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

OPERATORS = ("mobilis", "djezzy", "ooredoo")


class DashboardService:
    def __init__(self, db, api, config) -> None:
        self._db = db
        self._api = api
        self._config = config

        self._cache: Dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._response_times: Deque[float] = deque(maxlen=20)

    @property
    def config(self):
        return self._config

    # ── OneClick-derived facts (TTL-cached) ─────────────────────────────

    def _cache_is_fresh(self) -> bool:
        return (
            bool(self._cache)
            and (time.monotonic() - self._cache_ts) < self._config.DASHBOARD_CACHE_TTL
        )

    async def refresh_diagnostics(self, force: bool = False) -> Dict[str, Any]:
        """
        Refresh (or return cached) OneClick-derived diagnostic facts:
        wallet balance, reachability, per-operator offer counts, and a
        rolling average response time. Only calls OneClick if the cache is
        stale or `force` is True (dashboard's manual refresh button).
        """
        if not force and self._cache_is_fresh():
            return self._cache

        started = time.monotonic()
        balance_result = await self._api.get_account_balance()
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self._response_times.append(elapsed_ms)

        reachable = bool(balance_result.get("success"))
        wallet_balance = float(balance_result.get("balance", 0.0)) if reachable else None
        error_message = balance_result.get("error_message", "")

        offer_counts: Dict[str, int] = {}
        for operator in OPERATORS:
            plans_result = await self._api.get_fixed_plans(operator)
            offer_counts[operator] = (
                len(plans_result.get("plans", [])) if plans_result.get("success") else 0
            )

        self._cache = {
            "reachable": reachable,
            "wallet_balance": wallet_balance,
            "error_message": error_message,
            "offer_counts": offer_counts,
            "avg_response_ms": (
                sum(self._response_times) / len(self._response_times)
                if self._response_times else None
            ),
            "last_checked_ts": time.time(),
        }
        self._cache_ts = time.monotonic()
        return self._cache

    # ── Health score & alerts ───────────────────────────────────────────

    async def compute_health_score(self) -> Dict[str, Any]:
        """
        Weighted 0-100 health score with human-readable degradation reasons.
        Weights: OneClick reachability 40, wallet balance 25,
        recent failure rate 25, offer sync freshness 10.
        """
        diag = await self.refresh_diagnostics()
        failure = await self._db.get_recent_failure_rate(self._config.FAILURE_RATE_WINDOW_HOURS)

        score = 0
        reasons: List[str] = []

        if diag["reachable"]:
            score += 40
        else:
            reasons.append(f"OneClick API unreachable ({diag['error_message'] or 'unknown error'})")

        wallet = diag["wallet_balance"]
        if wallet is not None:
            if wallet >= self._config.WALLET_ALERT_THRESHOLD:
                score += 25
            elif wallet > 0:
                score += 10
                reasons.append(f"OneClick wallet balance low ({wallet:.2f})")
            else:
                reasons.append("OneClick wallet balance depleted")
        else:
            reasons.append("OneClick wallet balance unknown (API unreachable)")

        if failure["total"] == 0:
            score += 25
        elif failure["failure_pct"] <= self._config.FAILURE_RATE_ALERT_PCT:
            score += 25
        else:
            partial = max(0, 25 - int(failure["failure_pct"] / 4))
            score += partial
            reasons.append(
                f"Elevated failure rate: {failure['failure_pct']:.1f}% over last "
                f"{self._config.FAILURE_RATE_WINDOW_HOURS}h"
            )

        if any(count > 0 for count in diag["offer_counts"].values()):
            score += 10
        else:
            reasons.append("No live Activy offers found for any operator")

        return {"score": score, "reasons": reasons, "diagnostics": diag, "failure": failure}

    async def get_active_alerts(self) -> List[Dict[str, str]]:
        """
        Returns a list of {severity, message} for anything needing admin
        attention. Reused by both the "Action Required" section on Home and
        the full Alerts screen.
        """
        diag = await self.refresh_diagnostics()
        failure = await self._db.get_recent_failure_rate(self._config.FAILURE_RATE_WINDOW_HOURS)
        alerts: List[Dict[str, str]] = []

        if not diag["reachable"]:
            alerts.append({
                "severity": "critical",
                "message": f"OneClick API unreachable: {diag['error_message'] or 'unknown error'}",
            })

        wallet = diag["wallet_balance"]
        if wallet is not None and wallet < self._config.WALLET_ALERT_THRESHOLD:
            severity = "critical" if wallet <= 0 else "warning"
            alerts.append({
                "severity": severity,
                "message": f"Low OneClick wallet balance: {wallet:.2f}",
            })

        if failure["total"] > 0 and failure["failure_pct"] > self._config.FAILURE_RATE_ALERT_PCT:
            alerts.append({
                "severity": "warning",
                "message": (
                    f"Abnormal failure rate: {failure['failure_pct']:.1f}% "
                    f"({failure['failed']}/{failure['total']}) over last "
                    f"{self._config.FAILURE_RATE_WINDOW_HOURS}h"
                ),
            })

        zero_offer_ops = [op for op, cnt in diag["offer_counts"].items() if cnt == 0]
        if zero_offer_ops and diag["reachable"]:
            alerts.append({
                "severity": "warning",
                "message": f"No live offers for: {', '.join(zero_offer_ops)}",
            })

        return alerts

    # ── Last Activity (fresh every call, by design — see module docstring) ──

    @staticmethod
    def mask_phone(phone: Optional[str]) -> str:
        if not phone:
            return "—"
        phone = str(phone)
        if len(phone) <= 4:
            return phone
        return f"{phone[:4]}{'*' * (len(phone) - 6)}{phone[-2:]}" if len(phone) > 6 else phone

    async def get_last_activity(self) -> Optional[Dict[str, Any]]:
        tx = await self._db.get_last_successful_transaction()
        if not tx:
            return None
        return {
            "phone_masked": self.mask_phone(tx.get("phone")),
            "operator": tx.get("operator") or "—",
            "type": tx.get("type"),
            "amount": tx.get("amount"),
            "description": tx.get("description"),
            "created_at": tx.get("created_at"),
        }

    # ── Home / Statistics / Diagnostics assembly ────────────────────────

    async def get_home_summary(self) -> Dict[str, Any]:
        health = await self.compute_health_score()
        alerts = await self.get_active_alerts()
        last_activity = await self.get_last_activity()
        today_start = datetime.utcnow().strftime("%Y-%m-%d 00:00:00")
        today_end = datetime.utcnow().strftime("%Y-%m-%d 23:59:59")
        today_stats = await self._db.get_period_stats(today_start, today_end)
        total_users = await self._db.count_users()
        total_transactions = await self._db.count_all_transactions()
        processing_transactions = await self._db.count_inflight_transactions()
        return {
            "health": health,
            "alerts": alerts,
            "last_activity": last_activity,
            "today_stats": today_stats,
            "total_users": total_users,
            "total_transactions": total_transactions,
            "processing_transactions": processing_transactions,
        }

    @staticmethod
    def period_bounds(period: str) -> Tuple[str, str, str]:
        """Returns (start_iso, end_iso, label) for a named period."""
        now = datetime.utcnow()
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), "Today"
        if period == "yesterday":
            y = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = y + timedelta(days=1)
            return y.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), "Yesterday"
        if period == "7days":
            start = now - timedelta(days=7)
            return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), "Last 7 Days"
        if period == "30days":
            start = now - timedelta(days=30)
            return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), "Last 30 Days"
        # Fallback: today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), "Today"

    async def get_statistics(
        self, period: str = "today", custom_start: Optional[str] = None, custom_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        if period == "custom" and custom_start and custom_end:
            start_iso, end_iso, label = custom_start, custom_end, "Custom Range"
        else:
            start_iso, end_iso, label = self.period_bounds(period)
        stats = await self._db.get_period_stats(start_iso, end_iso)
        new_users = await self._db.count_new_users_since(start_iso)
        new_favorites = await self._db.count_new_favorites_since(start_iso)
        return {"label": label, "start": start_iso, "end": end_iso, "stats": stats,
                "new_users": new_users, "new_favorites": new_favorites}

    async def get_diagnostics(self, force: bool = False) -> Dict[str, Any]:
        diag = await self.refresh_diagnostics(force=force)
        failure = await self._db.get_recent_failure_rate(self._config.FAILURE_RATE_WINDOW_HOURS)
        return {"diagnostics": diag, "failure": failure}
