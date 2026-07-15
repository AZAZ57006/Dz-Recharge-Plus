import os
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Core
    BOT_TOKEN: str
    ADMIN_IDS: List[int]
    MOCK_MODE: bool
    DATABASE_PATH: str
    LOG_FILE: str
    LOG_LEVEL: str

    # OneClick API
    ONECLICK_API_URL: str
    ONECLICK_API_KEY: str
    API_TIMEOUT: int        # seconds per request
    API_MAX_RETRIES: int    # max retry attempts on transient errors

    # Rate limiting (per user, per window)
    RATE_LIMIT_MAX: int     # max actions in window
    RATE_LIMIT_WINDOW: int  # window in seconds

    # Favorite Numbers (address book)
    FAVORITES_LIMIT: int    # max saved favorites per user

    # Transaction History
    HISTORY_PAGE_SIZE: int  # rows per page on the history list screen

    # Admin Dashboard (read-only reporting/diagnostics — see dashboard.py)
    DASHBOARD_CACHE_TTL: int         # seconds OneClick-derived facts stay cached
    WALLET_ALERT_THRESHOLD: float    # OneClick balance below this triggers an alert
    FAILURE_RATE_ALERT_PCT: float    # failure % over FAILURE_RATE_WINDOW_HOURS triggers an alert
    FAILURE_RATE_WINDOW_HOURS: int   # rolling window used for the failure-rate alert

    # Operations Center (read-only live ops view — see ops_center.py)
    OPS_CENTER_SLOW_THRESHOLD_SECONDS: int  # in-flight tx waiting longer than this is flagged "slow"

    # Distributor Management System — Phase 1 (see distributors.py)
    DISTRIBUTOR_LIST_PAGE_SIZE: int  # rows per page on the admin distributor list/search screen

    # Distributor Management System — Phase 2 (Wallet & Ledger)
    DISTRIBUTOR_LEDGER_PAGE_SIZE: int  # rows per page on the admin distributor ledger screen


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise ValueError("BOT_TOKEN environment variable is required.")

    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids: List[int] = []
    for x in admin_ids_str.split(","):
        stripped = x.strip()
        if not stripped:
            continue
        try:
            admin_ids.append(int(stripped))
        except ValueError:
            import sys
            print(
                f"[CONFIG] WARNING: could not parse ADMIN_IDS token {stripped!r} as int — skipped",
                file=sys.stderr,
            )

    return Config(
        BOT_TOKEN=token,
        ADMIN_IDS=admin_ids,
        MOCK_MODE=os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "bot.db"),
        LOG_FILE=os.getenv("LOG_FILE", "bot.log"),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),

        ONECLICK_API_URL=os.getenv("ONECLICK_API_URL", "https://api.oneclickdz.com/v3"),
        ONECLICK_API_KEY=os.getenv("ONECLICK_API_KEY", ""),
        API_TIMEOUT=int(os.getenv("API_TIMEOUT", "30")),
        API_MAX_RETRIES=int(os.getenv("API_MAX_RETRIES", "3")),

        RATE_LIMIT_MAX=int(os.getenv("RATE_LIMIT_MAX", "15")),
        RATE_LIMIT_WINDOW=int(os.getenv("RATE_LIMIT_WINDOW", "60")),

        FAVORITES_LIMIT=int(os.getenv("FAVORITES_LIMIT", "30")),

        HISTORY_PAGE_SIZE=int(os.getenv("HISTORY_PAGE_SIZE", "8")),

        DASHBOARD_CACHE_TTL=int(os.getenv("DASHBOARD_CACHE_TTL", "60")),
        WALLET_ALERT_THRESHOLD=float(os.getenv("WALLET_ALERT_THRESHOLD", "500")),
        FAILURE_RATE_ALERT_PCT=float(os.getenv("FAILURE_RATE_ALERT_PCT", "20")),
        FAILURE_RATE_WINDOW_HOURS=int(os.getenv("FAILURE_RATE_WINDOW_HOURS", "24")),

        OPS_CENTER_SLOW_THRESHOLD_SECONDS=int(os.getenv("OPS_CENTER_SLOW_THRESHOLD_SECONDS", "30")),

        DISTRIBUTOR_LIST_PAGE_SIZE=int(os.getenv("DISTRIBUTOR_LIST_PAGE_SIZE", "8")),
        DISTRIBUTOR_LEDGER_PAGE_SIZE=int(os.getenv("DISTRIBUTOR_LEDGER_PAGE_SIZE", "8")),
    )
