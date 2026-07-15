import aiosqlite
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id   INTEGER UNIQUE NOT NULL,
    username      TEXT,
    full_name     TEXT,
    balance       REAL    DEFAULT 0.0,
    language      TEXT    DEFAULT 'ar',
    is_banned     INTEGER DEFAULT 0,
    created_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    type         TEXT    NOT NULL,
    phone        TEXT,
    amount       REAL    NOT NULL,
    description  TEXT,
    status       TEXT    DEFAULT 'pending',
    api_response TEXT,
    operator     TEXT,
    reference    TEXT,
    tracking_chat_id    INTEGER,
    tracking_message_id INTEGER,
    created_at   TEXT    DEFAULT (datetime('now')),
    updated_at   TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS deposit_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    amount       REAL    NOT NULL,
    status       TEXT    DEFAULT 'pending',
    admin_note   TEXT,
    resolved_at  TEXT,
    created_at   TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT    NOT NULL,
    details    TEXT,
    level      TEXT    DEFAULT 'INFO',
    created_at TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per confirm-button token. INSERTing a token is used as an atomic
-- "claim" so a recharge confirmation can only ever be executed once, even if
-- the same callback is delivered twice (Telegram retry, user double-tap).
CREATE TABLE IF NOT EXISTS idempotency_keys (
    token      TEXT PRIMARY KEY,
    status     TEXT    DEFAULT 'processing',
    created_at TEXT    DEFAULT (datetime('now'))
);

-- Saved phone-number address book entries ("Favorite Numbers"). Purely a
-- presentation/convenience layer on top of the existing message-driven
-- recharge/Activy entry points — selecting a favorite only ever feeds its
-- phone number into those unmodified flows, it never bypasses them.
CREATE TABLE IF NOT EXISTS favorites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    label         TEXT    NOT NULL,
    phone         TEXT    NOT NULL,
    created_at    TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    last_used_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, phone)
);

-- Distributor Management System — Phase 1 (Foundation).
-- Purely additive: a distributor is a separate role/profile layered on top
-- of a Telegram identity, never merged into `users` or its balance/logic.
-- No FK to transactions yet (that is deliberately deferred to a later
-- phase) — Phase 1 is limited to profile + read-only wallet balance +
-- admin create/suspend/activate/list/search, per the approved scope.
CREATE TABLE IF NOT EXISTS distributors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id   INTEGER UNIQUE NOT NULL,
    full_name     TEXT    NOT NULL,
    username      TEXT,
    phone         TEXT,
    wallet_balance REAL   DEFAULT 0.0,
    status        TEXT    DEFAULT 'active',
    created_at    TEXT    DEFAULT (datetime('now')),
    created_by_admin_id INTEGER,
    last_activity_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_user   ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_deposits_status     ON deposit_requests(status);
CREATE INDEX IF NOT EXISTS idx_logs_user           ON logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_action         ON logs(action);
CREATE INDEX IF NOT EXISTS idx_favorites_user       ON favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_favorites_last_used  ON favorites(user_id, last_used_at);
CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user_phone   ON transactions(user_id, phone);

-- Global (not per-user) index used only by the read-only Admin Dashboard's
-- aggregate queries (today's counts, statistics periods, failure-rate
-- checks). Pure index addition — does not change any write path, table
-- shape, or business logic.
CREATE INDEX IF NOT EXISTS idx_transactions_created_status ON transactions(created_at, status);

-- Distributor Management System — Phase 1 indexes (additive, read-only usage).
CREATE INDEX IF NOT EXISTS idx_distributors_telegram_id ON distributors(telegram_id);
CREATE INDEX IF NOT EXISTS idx_distributors_status      ON distributors(status);

-- Distributor Management System — Phase 2 (Wallet & Ledger).
--
-- ACCOUNTING CONTRACT (must never be violated):
--   • distributor_ledger is the immutable accounting source of truth.
--   • distributors.wallet_balance is a derived, read-optimised cache of the
--     ledger.  It must always equal SUM(amount) over all ledger rows for
--     that distributor.  The two are updated atomically inside a single
--     BEGIN IMMEDIATE transaction — they cannot diverge under normal
--     operation.
--   • Ledger rows are append-only: no UPDATE, no DELETE, ever.
--   • All runtime balance reads use distributors.wallet_balance (O(1)).
--     SUM(amount) replay is reserved for audit / reconciliation only.
CREATE TABLE IF NOT EXISTS distributor_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    distributor_id   INTEGER NOT NULL REFERENCES distributors(id),
    amount           REAL    NOT NULL,          -- positive=credit, negative=debit; never 0
    balance_before   REAL    NOT NULL,          -- snapshot at write time
    balance_after    REAL    NOT NULL,          -- always balance_before + amount
    operation_type   TEXT    NOT NULL,          -- admin_credit | admin_debit | … (future types additive)
    reference_type   TEXT,                      -- manual | recharge_tx | … ; NULL = no reference
    reference_value  TEXT,                      -- the actual ID/code; NULL iff reference_type IS NULL
    idempotency_key  TEXT,                      -- caller-supplied dedup token; NULL for manual admin ops
    created_by       INTEGER NOT NULL,          -- Telegram ID of admin; 0 = system/scheduler
    created_source   TEXT    NOT NULL DEFAULT 'telegram_admin',  -- telegram_admin | api | scheduler | system | migration
    notes            TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dledger_distributor  ON distributor_ledger(distributor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dledger_operation    ON distributor_ledger(operation_type);
CREATE INDEX IF NOT EXISTS idx_dledger_created_by   ON distributor_ledger(created_by);
CREATE INDEX IF NOT EXISTS idx_dledger_reference    ON distributor_ledger(reference_type, reference_value);
CREATE INDEX IF NOT EXISTS idx_dledger_idempotency  ON distributor_ledger(idempotency_key);
"""


# Expected columns per table, derived from CREATE_TABLES_SQL above.
# Used by `_migrate()` to detect and add any column missing from an
# existing (already-created) database file. Constraints such as
# UNIQUE / NOT NULL / PRIMARY KEY / FOREIGN KEY are intentionally omitted
# here because SQLite's ALTER TABLE ... ADD COLUMN cannot add them
# retroactively to a table that may already contain rows.
EXPECTED_COLUMNS: Dict[str, List[tuple]] = {
    "users": [
        ("telegram_id", "INTEGER"),
        ("username", "TEXT"),
        ("full_name", "TEXT"),
        ("balance", "REAL DEFAULT 0.0"),
        ("language", "TEXT DEFAULT 'ar'"),
        ("is_banned", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "transactions": [
        ("user_id", "INTEGER"),
        ("type", "TEXT"),
        ("phone", "TEXT"),
        ("amount", "REAL"),
        ("description", "TEXT"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("api_response", "TEXT"),
        ("operator", "TEXT"),
        ("reference", "TEXT"),
        ("tracking_chat_id", "INTEGER"),
        ("tracking_message_id", "INTEGER"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT"),
    ],
    "deposit_requests": [
        ("user_id", "INTEGER"),
        ("amount", "REAL"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("admin_note", "TEXT"),
        ("resolved_at", "TEXT"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "logs": [
        ("user_id", "INTEGER"),
        ("action", "TEXT"),
        ("details", "TEXT"),
        ("level", "TEXT DEFAULT 'INFO'"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "settings": [
        ("value", "TEXT"),
    ],
    "favorites": [
        ("user_id", "INTEGER"),
        ("label", "TEXT"),
        ("phone", "TEXT"),
        ("created_at", "TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))"),
        ("last_used_at", "TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))"),
    ],
    "distributors": [
        ("telegram_id", "INTEGER"),
        ("full_name", "TEXT"),
        ("username", "TEXT"),
        ("phone", "TEXT"),
        ("wallet_balance", "REAL DEFAULT 0.0"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("created_by_admin_id", "INTEGER"),
        ("last_activity_at", "TEXT"),
        ("wallet_updated_at", "TEXT"),      # Phase 2: set on every credit/debit
    ],
    # Phase 2: append-only financial ledger — see accounting contract in CREATE_TABLES_SQL.
    "distributor_ledger": [
        ("distributor_id", "INTEGER"),
        ("amount", "REAL"),
        ("balance_before", "REAL"),
        ("balance_after", "REAL"),
        ("operation_type", "TEXT"),
        ("reference_type", "TEXT"),
        ("reference_value", "TEXT"),
        ("idempotency_key", "TEXT"),
        ("created_by", "INTEGER"),
        ("created_source", "TEXT DEFAULT 'telegram_admin'"),
        ("notes", "TEXT"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
}


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(CREATE_TABLES_SQL)
        await self._db.commit()
        await self._migrate()
        logger.info("Database initialised at %s", self._path)

    async def _migrate(self) -> None:
        """Ensure every column declared in EXPECTED_COLUMNS exists on the live database.

        For each table, compares its expected columns (mirroring CREATE_TABLES_SQL)
        against the live schema via PRAGMA table_info, and idempotently adds any
        missing column with ALTER TABLE ... ADD COLUMN. This covers columns added
        to the schema after a database file was already created on disk.
        """
        assert self._db
        for table, columns in EXPECTED_COLUMNS.items():
            async with self._db.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] async for row in cur}
            for col_name, col_def in columns:
                if col_name not in existing:
                    await self._db.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                    )
                    await self._db.commit()
                    logger.info(
                        "Migration applied: added %s.%s column", table, col_name
                    )

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        assert self._db
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        assert self._db
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        assert self._db
        async with self._db.execute(sql, params) as cur:
            await self._db.commit()
            return cur.lastrowid or 0

    # ── users ────────────────────────────────────────────────────────────────

    async def get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str],
        full_name: str,
    ) -> Dict[str, Any]:
        user = await self._fetchone(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        if user:
            await self._execute(
                "UPDATE users SET username = ?, full_name = ? WHERE telegram_id = ?",
                (username, full_name, telegram_id),
            )
            user["username"] = username
            user["full_name"] = full_name
            return user

        uid = await self._execute(
            "INSERT INTO users (telegram_id, username, full_name) VALUES (?, ?, ?)",
            (telegram_id, username, full_name),
        )
        return {
            "id": uid, "telegram_id": telegram_id,
            "username": username, "full_name": full_name,
            "balance": 0.0, "language": "ar",
            "is_banned": 0, "created_at": datetime.utcnow().isoformat(),
        }

    async def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )

    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    async def set_language(self, telegram_id: int, lang: str) -> None:
        await self._execute(
            "UPDATE users SET language = ? WHERE telegram_id = ?", (lang, telegram_id)
        )

    async def get_balance(self, telegram_id: int) -> float:
        row = await self._fetchone(
            "SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return float(row["balance"]) if row else 0.0

    async def adjust_balance(self, telegram_id: int, delta: float) -> float:
        await self._execute(
            "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
            (delta, telegram_id),
        )
        return await self.get_balance(telegram_id)

    async def set_balance(self, telegram_id: int, amount: float) -> None:
        await self._execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?", (amount, telegram_id)
        )

    async def ban_user(self, telegram_id: int, banned: bool = True) -> None:
        await self._execute(
            "UPDATE users SET is_banned = ? WHERE telegram_id = ?",
            (1 if banned else 0, telegram_id),
        )

    async def get_all_users(self) -> List[Dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM users ORDER BY created_at DESC"
        )

    async def count_users(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) as cnt FROM users")
        return row["cnt"] if row else 0

    # ── transactions ─────────────────────────────────────────────────────────

    async def create_transaction(
        self,
        user_id: int,
        tx_type: str,
        amount: float,
        description: str,
        phone: Optional[str] = None,
        status: str = "pending",
        operator: Optional[str] = None,
    ) -> int:
        return await self._execute(
            """INSERT INTO transactions
               (user_id, type, phone, amount, description, status, operator)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, tx_type, phone, amount, description, status, operator),
        )

    async def update_transaction(
        self,
        tx_id: int,
        status: str,
        api_response: Optional[str] = None,
        reference: Optional[str] = None,
    ) -> None:
        await self._execute(
            "UPDATE transactions SET status=?, api_response=?, reference=?, "
            "updated_at=datetime('now') WHERE id=?",
            (status, api_response, reference, tx_id),
        )

    async def get_user_transactions(
        self, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        return await self._fetchall(
            """SELECT * FROM transactions WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )

    async def get_all_transactions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # ── Professional transaction history (read-only reporting layer) ───────
    #
    # These methods only ever SELECT from `transactions` (LEFT JOINed against
    # `favorites` purely to show/search a saved name) — they never write to
    # transactions, wallet balance, or any recharge/OneClick state. The one
    # exception, `get_transaction`, is used by the "Repeat" action, which
    # only re-renders the existing confirm screen / Activy offers screen —
    # it does not perform any recharge itself.

    @staticmethod
    def _history_filters_sql(
        search: Optional[str], date_filter: Optional[str], status_filter: Optional[str],
    ) -> tuple:
        clauses = ["t.user_id = ?"]
        params: List[Any] = []

        if search:
            clauses.append("(t.phone LIKE ? OR f.label LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])

        if date_filter == "today":
            clauses.append("date(t.created_at) = date('now')")
        elif date_filter == "yesterday":
            clauses.append("date(t.created_at) = date('now', '-1 day')")
        elif date_filter == "7days":
            clauses.append("date(t.created_at) >= date('now', '-6 day')")

        if status_filter == "success":
            clauses.append("t.status = 'success'")
        elif status_filter == "failed":
            clauses.append("t.status = 'failed'")

        return clauses, params

    async def list_transactions_paginated(
        self,
        user_id: int,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        date_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses, params = self._history_filters_sql(search, date_filter, status_filter)
        where = " AND ".join(clauses)
        params_full = [user_id, *params, limit, offset]
        return await self._fetchall(
            f"""SELECT t.*, f.label AS favorite_label
                FROM transactions t
                LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
                WHERE {where}
                ORDER BY t.created_at DESC
                LIMIT ? OFFSET ?""",
            tuple(params_full),
        )

    async def count_transactions_filtered(
        self,
        user_id: int,
        search: Optional[str] = None,
        date_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> int:
        clauses, params = self._history_filters_sql(search, date_filter, status_filter)
        where = " AND ".join(clauses)
        row = await self._fetchone(
            f"""SELECT COUNT(*) as cnt
                FROM transactions t
                LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
                WHERE {where}""",
            tuple([user_id, *params]),
        )
        return row["cnt"] if row else 0

    async def get_transaction(self, tx_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Scoped by user_id so one user can never view another's transaction."""
        return await self._fetchone(
            """SELECT t.*, f.label AS favorite_label
               FROM transactions t
               LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
               WHERE t.id = ? AND t.user_id = ?""",
            (tx_id, user_id),
        )

    # ── Asynchronous recharge tracking (infra-only, additive) ───────────────
    #
    # These methods only ever read/write the two new tracking_* columns and
    # the existing `status` column via the existing update_transaction() —
    # they never touch amount/balance/reference/api_response write paths
    # used by RechargeService, and never bypass or duplicate its logic.

    async def get_latest_inflight_transaction_id(self, user_id: int, phone: str) -> Optional[int]:
        """Read-only lookup used only to attach a tracking card to the
        transaction row that RechargeService just created for this
        confirm tap. Never creates/updates a transaction itself."""
        row = await self._fetchone(
            """SELECT id FROM transactions
               WHERE user_id = ? AND phone = ? AND status IN ('pending', 'processing')
               ORDER BY id DESC LIMIT 1""",
            (user_id, phone),
        )
        return row["id"] if row else None

    async def set_tracking_message(self, tx_id: int, chat_id: int, message_id: int) -> None:
        await self._execute(
            "UPDATE transactions SET tracking_chat_id = ?, tracking_message_id = ? WHERE id = ?",
            (chat_id, message_id, tx_id),
        )

    async def count_inflight_transactions(self) -> int:
        """Transactions still 'pending' or 'processing' — i.e. currently
        being tracked by the background RechargeTracker. Read-only, used by
        the Admin Dashboard's 'Processing Transactions' metric."""
        row = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM transactions WHERE status IN ('pending', 'processing')"
        )
        return row["cnt"] if row else 0

    async def get_stale_inflight_transactions(self, older_than_minutes: int) -> List[Dict[str, Any]]:
        """Transactions still 'pending'/'processing' whose created_at is
        older than `older_than_minutes` — used only at startup to recover
        transactions left in-flight by a previous process's shutdown/crash.
        """
        return await self._fetchall(
            """SELECT * FROM transactions
               WHERE status IN ('pending', 'processing')
                 AND created_at <= datetime('now', ?)""",
            (f"-{older_than_minutes} minutes",),
        )

    # ── Operations Center (read-only, additive) ─────────────────────────────
    #
    # These methods only ever SELECT. They never write anything and never
    # touch RechargeService/OneClickAPI/wallet logic. `updated_at` is set
    # exclusively by the existing update_transaction() above.

    async def get_inflight_transactions_ordered(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Transactions currently 'pending'/'processing', oldest first —
        used by the Operations Center's Processing Transactions list."""
        return await self._fetchall(
            """SELECT t.*, f.label AS favorite_label
               FROM transactions t
               LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
               WHERE t.status IN ('pending', 'processing')
               ORDER BY t.created_at ASC
               LIMIT ?""",
            (limit,),
        )

    async def get_last_completed_transactions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Most recent resolved (success/failed) transactions — used by the
        Operations Center's Last Completed list."""
        return await self._fetchall(
            """SELECT t.*, f.label AS favorite_label
               FROM transactions t
               LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
               WHERE t.status IN ('success', 'failed')
               ORDER BY t.created_at DESC
               LIMIT ?""",
            (limit,),
        )

    async def get_avg_completion_seconds(self, minutes: int) -> Optional[float]:
        """Average seconds between created_at and updated_at for
        transactions resolved (success/failed) within the last `minutes`
        minutes. Returns None if there is no data yet (e.g. right after the
        additive updated_at column was introduced, or no completions in
        window)."""
        row = await self._fetchone(
            """SELECT AVG(
                 (julianday(updated_at) - julianday(created_at)) * 86400.0
               ) as avg_seconds
               FROM transactions
               WHERE status IN ('success', 'failed')
                 AND updated_at IS NOT NULL
                 AND created_at >= datetime('now', ?)""",
            (f"-{minutes} minutes",),
        )
        return float(row["avg_seconds"]) if row and row["avg_seconds"] is not None else None

    async def get_transaction_admin(self, tx_id: int) -> Optional[Dict[str, Any]]:
        """Admin-only, read-only lookup by id with NO user_id scoping —
        callers MUST enforce admin authorization themselves (as the
        Operations Center handlers do). Used only to render the existing,
        unmodified history details text/keyboard for a transaction that may
        belong to any user."""
        return await self._fetchone(
            """SELECT t.*, f.label AS favorite_label
               FROM transactions t
               LEFT JOIN favorites f ON f.user_id = t.user_id AND f.phone = t.phone
               WHERE t.id = ?""",
            (tx_id,),
        )

    async def count_transactions(self) -> Dict[str, int]:
        rows = await self._fetchall(
            "SELECT status, COUNT(*) as cnt FROM transactions GROUP BY status"
        )
        result = {r["status"]: r["cnt"] for r in rows}
        result["total"] = sum(result.values())
        return result

    # ── Admin Dashboard (read-only aggregate reporting layer) ──────────────
    #
    # Every method below is a pure SELECT/aggregate over existing tables.
    # None of them write to transactions/users/favorites, and none of them
    # touch wallet balance, OneClick, or recharge/Activy processing — they
    # only summarize data that other, unmodified code paths already wrote.

    async def count_all_transactions(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) as cnt FROM transactions")
        return row["cnt"] if row else 0

    async def count_all_favorites(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) as cnt FROM favorites")
        return row["cnt"] if row else 0

    async def count_new_users_since(self, start_iso: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM users WHERE created_at >= ?", (start_iso,)
        )
        return row["cnt"] if row else 0

    async def count_new_favorites_since(self, start_iso: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM favorites WHERE created_at >= ?", (start_iso,)
        )
        return row["cnt"] if row else 0

    async def get_period_stats(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        """
        Aggregate stats for transactions with created_at in [start_iso, end_iso]
        (inclusive on both ends).
        Returns success/failed counts, total sales (sum of successful amounts),
        a per-operator breakdown, and per-type breakdown (standard/activy/game/
        gift_card) — all from a single pass over the indexed
        (created_at, status) range.
        """
        rows = await self._fetchall(
            """SELECT status, type, operator, amount
               FROM transactions
               WHERE created_at >= ? AND created_at <= ?""",
            (start_iso, end_iso),
        )
        success = sum(1 for r in rows if r["status"] == "success")
        failed = sum(1 for r in rows if r["status"] == "failed")
        sales = sum(r["amount"] for r in rows if r["status"] == "success")
        by_operator: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for r in rows:
            op = r["operator"] or "unknown"
            by_operator[op] = by_operator.get(op, 0) + 1
            t = r["type"] or "unknown"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total": len(rows),
            "success": success,
            "failed": failed,
            "sales": sales,
            "by_operator": by_operator,
            "by_type": by_type,
        }

    async def get_last_successful_transaction(self) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            """SELECT * FROM transactions
               WHERE status = 'success'
               ORDER BY created_at DESC LIMIT 1"""
        )

    async def get_recent_failure_rate(self, hours: int) -> Dict[str, Any]:
        """Success/failed counts and failure % over the last `hours` hours."""
        row = await self._fetchone(
            """SELECT
                 SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                 SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) as failed
               FROM transactions
               WHERE created_at >= datetime('now', ?)""",
            (f"-{hours} hours",),
        )
        success = (row["success"] or 0) if row else 0
        failed = (row["failed"] or 0) if row else 0
        total = success + failed
        pct = (failed / total * 100.0) if total else 0.0
        return {"success": success, "failed": failed, "total": total, "failure_pct": pct}

    # ── deposit requests ─────────────────────────────────────────────────────

    async def create_deposit_request(self, user_id: int, amount: float) -> int:
        return await self._execute(
            "INSERT INTO deposit_requests (user_id, amount) VALUES (?, ?)",
            (user_id, amount),
        )

    async def get_deposit_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            """SELECT d.*, u.telegram_id, u.full_name, u.username
               FROM deposit_requests d
               JOIN users u ON u.id = d.user_id
               WHERE d.id = ?""",
            (request_id,),
        )

    async def resolve_deposit(
        self,
        request_id: int,
        status: str,
        admin_note: str = "",
    ) -> Optional[Dict[str, Any]]:
        await self._execute(
            """UPDATE deposit_requests
               SET status=?, admin_note=?, resolved_at=datetime('now')
               WHERE id=?""",
            (status, admin_note, request_id),
        )
        return await self.get_deposit_request(request_id)

    async def get_pending_deposits(self) -> List[Dict[str, Any]]:
        return await self._fetchall(
            """SELECT d.*, u.telegram_id, u.full_name, u.balance
               FROM deposit_requests d
               JOIN users u ON u.id = d.user_id
               WHERE d.status = 'pending'
               ORDER BY d.created_at ASC""",
        )

    async def get_all_deposits(self, limit: int = 30) -> List[Dict[str, Any]]:
        return await self._fetchall(
            """SELECT d.*, u.telegram_id, u.full_name
               FROM deposit_requests d
               JOIN users u ON u.id = d.user_id
               ORDER BY d.created_at DESC LIMIT ?""",
            (limit,),
        )

    # ── logs ────────────────────────────────────────────────────────────────

    async def log(
        self,
        action: str,
        details: Optional[str] = None,
        user_id: Optional[int] = None,
        level: str = "INFO",
    ) -> None:
        try:
            await self._execute(
                "INSERT INTO logs (user_id, action, details, level) VALUES (?, ?, ?, ?)",
                (user_id, action, details, level),
            )
        except Exception as exc:
            logger.error("Failed to write audit log: %s", exc)

    async def get_recent_logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # ── settings ────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ── idempotency (duplicate-confirm protection) ─────────────────────────────

    async def claim_idempotency_key(self, token: str) -> bool:
        """
        Atomically claim `token` for processing.

        Returns True the first time a given token is claimed (caller may
        proceed). Returns False if the token was already claimed before
        (i.e. this is a duplicate/retried callback) — the caller must not
        repeat the underlying action (API call, balance deduction, etc).

        Relies on `token` being the PRIMARY KEY of idempotency_keys: the
        INSERT either succeeds once or raises IntegrityError on every
        subsequent attempt, which is safe even under concurrent callers
        since sqlite serialises writes on the connection.
        """
        assert self._db
        try:
            await self._db.execute(
                "INSERT INTO idempotency_keys (token, status) VALUES (?, 'processing')",
                (token,),
            )
            await self._db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def finish_idempotency_key(self, token: str, status: str) -> None:
        await self._execute(
            "UPDATE idempotency_keys SET status = ? WHERE token = ?",
            (status, token),
        )

    # ── favorites (address book) ────────────────────────────────────────────
    # Purely a saved-numbers convenience layer. These methods only ever read/
    # write the `favorites` table — they never touch users/transactions/
    # wallet balance/idempotency_keys, and callers feed the resulting phone
    # number into the existing, unmodified recharge/Activy entry points.

    async def count_favorites(self, user_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ?", (user_id,)
        )
        return row["cnt"] if row else 0

    async def add_favorite(self, user_id: int, label: str, phone: str) -> int:
        return await self._execute(
            "INSERT INTO favorites (user_id, label, phone) VALUES (?, ?, ?)",
            (user_id, label, phone),
        )

    async def get_favorite(self, favorite_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Scoped by user_id so one user can never read/rename/delete another's entry."""
        return await self._fetchone(
            "SELECT * FROM favorites WHERE id = ? AND user_id = ?", (favorite_id, user_id)
        )

    async def get_favorite_by_phone(self, user_id: int, phone: str) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            "SELECT * FROM favorites WHERE user_id = ? AND phone = ?", (user_id, phone)
        )

    async def list_favorites(
        self, user_id: int, sort: str = "recent"
    ) -> List[Dict[str, Any]]:
        order_by = "last_used_at DESC" if sort == "recent" else "label COLLATE NOCASE ASC"
        return await self._fetchall(
            f"SELECT * FROM favorites WHERE user_id = ? ORDER BY {order_by}", (user_id,)
        )

    async def search_favorites(self, user_id: int, query: str) -> List[Dict[str, Any]]:
        like = f"%{query}%"
        return await self._fetchall(
            """SELECT * FROM favorites WHERE user_id = ?
               AND (label LIKE ? OR phone LIKE ?)
               ORDER BY last_used_at DESC""",
            (user_id, like, like),
        )

    async def rename_favorite(self, favorite_id: int, user_id: int, new_label: str) -> None:
        await self._execute(
            "UPDATE favorites SET label = ? WHERE id = ? AND user_id = ?",
            (new_label, favorite_id, user_id),
        )

    async def delete_favorite(self, favorite_id: int, user_id: int) -> None:
        await self._execute(
            "DELETE FROM favorites WHERE id = ? AND user_id = ?", (favorite_id, user_id)
        )

    async def touch_favorite(self, favorite_id: int, user_id: int) -> None:
        """Bump last_used_at so 'Most Recently Used' sort reflects this selection."""
        await self._execute(
            "UPDATE favorites SET last_used_at = strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ? AND user_id = ?",
            (favorite_id, user_id),
        )

    # ── Distributor Management System — Phase 1 (Foundation) ───────────────
    #
    # Purely additive: separate table, no writes to users/transactions/
    # wallet/idempotency_keys/logs schema. `wallet_balance` here is a
    # read-only profile field in Phase 1 — there is no operation yet that
    # adjusts it (that is Phase 2 scope: wallet ledger/operations). This
    # layer never touches RechargeService/OneClickAPI/WalletService.

    async def create_distributor(
        self,
        telegram_id: int,
        full_name: str,
        username: Optional[str],
        phone: Optional[str],
        created_by_admin_id: int,
    ) -> int:
        return await self._execute(
            """INSERT INTO distributors
               (telegram_id, full_name, username, phone, created_by_admin_id)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_id, full_name, username, phone, created_by_admin_id),
        )

    async def get_distributor(self, distributor_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            "SELECT * FROM distributors WHERE id = ?", (distributor_id,)
        )

    async def get_distributor_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            "SELECT * FROM distributors WHERE telegram_id = ?", (telegram_id,)
        )

    async def set_distributor_status(self, distributor_id: int, status: str) -> None:
        await self._execute(
            "UPDATE distributors SET status = ? WHERE id = ?", (status, distributor_id)
        )

    async def touch_distributor_activity(self, telegram_id: int) -> None:
        """Bump last_activity_at. Best-effort, read-adjacent bookkeeping only —
        never called from a money-moving path, so it can never race with or
        block recharge/wallet logic."""
        await self._execute(
            "UPDATE distributors SET last_activity_at = datetime('now') WHERE telegram_id = ?",
            (telegram_id,),
        )

    async def list_distributors(
        self, limit: int = 20, offset: int = 0, status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        return await self._fetchall(
            f"""SELECT * FROM distributors {where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            tuple(params),
        )

    async def count_distributors(self, status_filter: Optional[str] = None) -> int:
        if status_filter:
            row = await self._fetchone(
                "SELECT COUNT(*) as cnt FROM distributors WHERE status = ?", (status_filter,)
            )
        else:
            row = await self._fetchone("SELECT COUNT(*) as cnt FROM distributors")
        return row["cnt"] if row else 0

    async def search_distributors(
        self, query: str, limit: int = 20, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        like = f"%{query}%"
        return await self._fetchall(
            """SELECT * FROM distributors
               WHERE full_name LIKE ? OR username LIKE ? OR phone LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (like, like, like, like, limit, offset),
        )

    async def count_search_distributors(self, query: str) -> int:
        like = f"%{query}%"
        row = await self._fetchone(
            """SELECT COUNT(*) as cnt FROM distributors
               WHERE full_name LIKE ? OR username LIKE ? OR phone LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?""",
            (like, like, like, like),
        )
        return row["cnt"] if row else 0

    # ── Distributor Management System — Phase 2 (Wallet & Ledger) ──────────
    #
    # ACCOUNTING CONTRACT — enforced here, never relaxed:
    #   1. wallet_balance (O(1) cache) and a new ledger row are written inside
    #      a single BEGIN IMMEDIATE transaction.  Either both succeed or
    #      neither does — they cannot diverge.
    #   2. distributor_ledger is append-only.  No UPDATE or DELETE ever
    #      touches it.  Corrections are always new rows.
    #   3. Runtime balance reads use distributors.wallet_balance exclusively.
    #      SUM(amount) replay is for audit/reconciliation only.
    #
    # Never call these from RechargeService / OneClickAPI / WalletService /
    # RechargeTracker or any recharge code path.

    async def _distributor_wallet_op(
        self,
        distributor_id: int,
        raw_amount: float,       # already signed: + for credit, - for debit
        operation_type: str,
        created_by: int,
        created_source: str = "telegram_admin",
        reference_type: Optional[str] = None,
        reference_value: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomic wallet operation.  Validates, writes ledger row, updates
        wallet_balance — all inside BEGIN IMMEDIATE.  Returns the new
        ledger entry as a plain dict.

        Raises ValueError with a descriptive key on any validation failure
        so callers can map to user-facing text without inspecting raw msgs.
        """
        assert self._db
        assert raw_amount != 0, "amount must not be zero"

        async with self._db.execute("BEGIN IMMEDIATE"):
            pass  # acquire the write lock

        try:
            # 1. Read current state under the write lock.
            row = await self._fetchone(
                "SELECT id, wallet_balance, status FROM distributors WHERE id = ?",
                (distributor_id,),
            )
            if not row:
                raise ValueError("distributor_not_found")
            if row["status"] != "active":
                raise ValueError("distributor_suspended")

            balance_before: float = row["wallet_balance"]
            balance_after: float = round(balance_before + raw_amount, 10)

            # 2. Business-rule validation.
            if balance_after < 0:
                raise ValueError("insufficient_balance")

            # 3. Append ledger row (never updated, never deleted).
            async with self._db.execute(
                """INSERT INTO distributor_ledger
                   (distributor_id, amount, balance_before, balance_after,
                    operation_type, reference_type, reference_value,
                    idempotency_key, created_by, created_source, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (distributor_id, raw_amount, balance_before, balance_after,
                 operation_type, reference_type, reference_value,
                 idempotency_key, created_by, created_source, notes),
            ) as cur:
                entry_id = cur.lastrowid

            # 4. Update the cached balance + timestamp.
            await self._db.execute(
                """UPDATE distributors
                   SET wallet_balance = ?, wallet_updated_at = datetime('now')
                   WHERE id = ?""",
                (balance_after, distributor_id),
            )

            await self._db.commit()

        except Exception:
            await self._db.rollback()
            raise

        entry = await self._fetchone(
            "SELECT * FROM distributor_ledger WHERE id = ?", (entry_id,)
        )
        assert entry is not None
        return entry

    async def distributor_wallet_credit(
        self,
        distributor_id: int,
        amount: float,
        created_by: int,
        created_source: str = "telegram_admin",
        reference_type: Optional[str] = None,
        reference_value: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Credit (add funds).  amount must be > 0."""
        if amount <= 0:
            raise ValueError("amount_must_be_positive")
        return await self._distributor_wallet_op(
            distributor_id=distributor_id,
            raw_amount=amount,
            operation_type="admin_credit",
            created_by=created_by,
            created_source=created_source,
            reference_type=reference_type,
            reference_value=reference_value,
            idempotency_key=idempotency_key,
            notes=notes,
        )

    async def distributor_wallet_debit(
        self,
        distributor_id: int,
        amount: float,
        created_by: int,
        created_source: str = "telegram_admin",
        reference_type: Optional[str] = None,
        reference_value: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Debit (remove funds).  amount must be > 0; raises if balance insufficient."""
        if amount <= 0:
            raise ValueError("amount_must_be_positive")
        return await self._distributor_wallet_op(
            distributor_id=distributor_id,
            raw_amount=-amount,
            operation_type="admin_debit",
            created_by=created_by,
            created_source=created_source,
            reference_type=reference_type,
            reference_value=reference_value,
            idempotency_key=idempotency_key,
            notes=notes,
        )

    async def get_distributor_ledger(
        self, distributor_id: int, limit: int = 8, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Newest-first page of ledger entries for one distributor."""
        return await self._fetchall(
            """SELECT * FROM distributor_ledger
               WHERE distributor_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (distributor_id, limit, offset),
        )

    async def count_distributor_ledger(self, distributor_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM distributor_ledger WHERE distributor_id = ?",
            (distributor_id,),
        )
        return row["cnt"] if row else 0

    async def get_distributor_ledger_entry(self, entry_id: int) -> Optional[Dict[str, Any]]:
        return await self._fetchone(
            "SELECT * FROM distributor_ledger WHERE id = ?", (entry_id,)
        )
