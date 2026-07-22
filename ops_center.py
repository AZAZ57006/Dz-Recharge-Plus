"""
ops_center.py — Operations Center: a live, read-only operational view for
admins, built on top of the existing Admin Dashboard.

This module never writes to the database (aside from reads) and never
touches RechargeService/OneClickAPI/wallet logic/RechargeTracker. It only:
  - reads OneClick-derived facts through DashboardService's existing
    60s TTL cache (refresh_diagnostics) — this module never calls
    OneClickAPI directly, so opening/browsing it never adds new OneClick
    calls beyond what the Dashboard already makes, and
  - reads cheap, indexed, read-only aggregate methods on Database (see the
    "Operations Center" section of database.py).

Structured as a small panel registry so future read-only modules (Store
Management, Wallet Management, Scheduled Tasks, Queue Workers, Smart
Alerts, ...) can be added as additional panels without touching the Home
assembly logic below.
"""

import time
from typing import Any, Dict, List, Optional


class OperationsCenterService:
    def __init__(self, db, dashboard_service, config) -> None:
        self._db = db
        self._dashboard = dashboard_service
        self._config = config

    @property
    def config(self):
        return self._config

    # ── Panels (each returns a small dict; Home composes them) ──────────

    async def _panel_queue(self) -> Dict[str, Any]:
        inflight = await self._db.get_inflight_transactions_ordered(limit=50)
        threshold = self._config.OPS_CENTER_SLOW_THRESHOLD_SECONDS
        now = time.time()
        slow_count = 0
        oldest_age_seconds: Optional[float] = None
        for tx in inflight:
            age = self._age_seconds(tx.get("created_at"), now)
            if age is not None:
                if oldest_age_seconds is None or age > oldest_age_seconds:
                    oldest_age_seconds = age
                if age >= threshold:
                    slow_count += 1
        return {
            "processing_count": len(inflight),
            "slow_count": slow_count,
            "oldest_age_seconds": oldest_age_seconds,
            "threshold_seconds": threshold,
        }

    async def _panel_throughput(self) -> Dict[str, Any]:
        stats_1h = await self._db.get_period_stats(
            *self._hour_bounds(1),
        )
        avg_completion = await self._db.get_avg_completion_seconds(minutes=60)
        return {
            "completed_1h": stats_1h["success"] + stats_1h["failed"],
            "success_1h": stats_1h["success"],
            "failed_1h": stats_1h["failed"],
            "avg_completion_seconds": avg_completion,
        }

    async def _panel_oneclick(self, force: bool = False) -> Dict[str, Any]:
        diag = await self._dashboard.refresh_diagnostics(force=force)
        return {
            "reachable": diag["reachable"],
            "wallet_balance": diag["wallet_balance"],
            "offer_counts": diag["offer_counts"],
            "last_checked_ts": diag.get("last_checked_ts"),
        }

    # ── Home assembly ─────────────────────────────────────────────────

    async def get_home_summary(self, force: bool = False) -> Dict[str, Any]:
        queue = await self._panel_queue()
        throughput = await self._panel_throughput()
        oneclick = await self._panel_oneclick(force=force)
        failure = await self._db.get_recent_failure_rate(1)

        pill = self._composite_health_pill(queue, oneclick, failure)

        return {
            "pill": pill,
            "queue": queue,
            "throughput": throughput,
            "oneclick": oneclick,
            "failure_1h": failure,
        }

    @staticmethod
    def _composite_health_pill(
        queue: Dict[str, Any], oneclick: Dict[str, Any], failure: Dict[str, Any],
    ) -> str:
        """🟢 all clear / 🟡 degraded / 🔴 critical — a fast at-a-glance
        summary combining OneClick reachability, slow in-flight items, and
        the last-hour failure rate. Purely presentational, no side effects."""
        if not oneclick["reachable"]:
            return "red"
        if queue["slow_count"] > 0:
            return "yellow"
        if failure["total"] > 0 and failure["failure_pct"] > 50:
            return "red"
        if failure["total"] > 0 and failure["failure_pct"] > 20:
            return "yellow"
        return "green"

    # ── Lists ─────────────────────────────────────────────────────────

    async def get_processing_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._db.get_inflight_transactions_ordered(limit=limit)

    async def get_completed_list(self, limit: int = 10) -> List[Dict[str, Any]]:
        return await self._db.get_last_completed_transactions(limit=limit)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _hour_bounds(hours: int):
        import datetime as _dt
        now = _dt.datetime.utcnow()
        start = now - _dt.timedelta(hours=hours)
        return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _age_seconds(created_at: Optional[str], now_ts: float) -> Optional[float]:
        if not created_at:
            return None
        import datetime as _dt
        try:
            created = _dt.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        created_ts = created.replace(tzinfo=_dt.timezone.utc).timestamp()
        return max(0.0, now_ts - created_ts)
