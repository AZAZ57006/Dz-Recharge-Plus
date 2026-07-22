"""
distributors.py — Distributor Management System, Phases 1–3.

Phase 1 scope: distributor profile, role resolution, read-only wallet
  balance display, admin create/activate/suspend/list/search.

Phase 2 scope: DistributorWalletService — admin credit/debit via an
  append-only financial ledger.  wallet_balance on the distributors table
  is the O(1) runtime balance cache; distributor_ledger is the immutable
  accounting source of truth.  All wallet mutations are atomic.

Phase 3 scope: DistributorRechargeService — distributor-funded recharge
  and Activy activation.  Composes OneClickAPI (unchanged), Database
  transaction methods (unchanged), and DistributorWalletService.debit()
  (Phase 2).  Wallet is deducted ONLY on confirmed OneClick success.

Explicitly OUT of scope for Phases 1-3 (deferred):
  - commissions, hierarchy, regional managers (Phase 4+)

DistributorRechargeService never touches RechargeService or WalletService.
RechargeService is left completely unchanged.
"""

import logging
from typing import Any, Dict, List, Optional

from database import Database

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_DISTRIBUTOR = "distributor"
ROLE_CUSTOMER = "customer"

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"


async def resolve_role(telegram_id: int, db: Database, admin_ids: List[int]) -> str:
    """
    Resolve the highest applicable role for menu/keyboard purposes.

    Roles are additive, not exclusive (an admin or distributor is still a
    customer too) — this only picks which extra UI surface to show. Every
    sensitive action must still re-check its own specific requirement
    (e.g. admin-only, active-distributor-only) at the point of use rather
    than trusting this resolver alone, per the approved permission design.
    """
    if telegram_id in admin_ids:
        return ROLE_ADMIN
    distributor = await db.get_distributor_by_telegram_id(telegram_id)
    if distributor and distributor["status"] == STATUS_ACTIVE:
        return ROLE_DISTRIBUTOR
    return ROLE_CUSTOMER


class DistributorService:
    """Thin, read/write-scoped service wrapping the `distributors` table.
    Telegram-agnostic (takes/returns plain dicts) so it can later be reused
    by a non-Telegram frontend (e.g. a future web dashboard) without
    modification — mirrors how DashboardService/OperationsCenterService are
    already structured."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        telegram_id: int,
        full_name: str,
        username: Optional[str],
        phone: Optional[str],
        created_by_admin_id: int,
    ) -> Dict[str, Any]:
        existing = await self._db.get_distributor_by_telegram_id(telegram_id)
        if existing:
            raise ValueError("already_exists")
        distributor_id = await self._db.create_distributor(
            telegram_id, full_name, username, phone, created_by_admin_id
        )
        await self._db.log(
            "distributor_created",
            f"distributor_id={distributor_id} telegram_id={telegram_id}",
            level="INFO",
        )
        created = await self._db.get_distributor(distributor_id)
        assert created is not None
        return created

    async def get(self, distributor_id: int) -> Optional[Dict[str, Any]]:
        return await self._db.get_distributor(distributor_id)

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        return await self._db.get_distributor_by_telegram_id(telegram_id)

    async def set_status(self, distributor_id: int, status: str) -> None:
        if status not in (STATUS_ACTIVE, STATUS_SUSPENDED):
            raise ValueError("invalid_status")
        await self._db.set_distributor_status(distributor_id, status)
        await self._db.log(
            "distributor_status_changed",
            f"distributor_id={distributor_id} status={status}",
            level="INFO",
        )

    async def list(
        self, limit: int = 10, offset: int = 0, status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await self._db.list_distributors(limit, offset, status_filter)

    async def count(self, status_filter: Optional[str] = None) -> int:
        return await self._db.count_distributors(status_filter)

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        return await self._db.search_distributors(query, limit, offset)

    async def count_search(self, query: str) -> int:
        return await self._db.count_search_distributors(query)


# ---------------------------------------------------------------------------
# Phase 2 — Distributor Wallet & Financial Ledger
#
# ACCOUNTING CONTRACT (mirrored from database.py — must never be violated):
#   • distributor_ledger is the immutable accounting source of truth.
#   • distributors.wallet_balance is the O(1) runtime balance cache.
#     Every runtime balance read must use wallet_balance, never SUM(ledger).
#   • SUM(amount) replay is reserved for audit/reconciliation only.
#   • Ledger entries are append-only: no UPDATE, no DELETE, ever.
#   • Corrections are always new ledger entries (never edits to existing ones).
#   • Every wallet mutation is atomic: ledger row + wallet_balance update
#     succeed together or neither change.
#
# This service never touches RechargeService, OneClickAPI, WalletService,
# RechargeTracker, or any customer recharge/Activy code path.
# ---------------------------------------------------------------------------

# Operation type constants — used for ledger entries.
# Phase 2 implements admin_credit / admin_debit.
# Phase 3 adds recharge_debit.
# Future types (commission_credit, refund_credit, …) are added by new
# phases without schema changes.
OP_ADMIN_CREDIT      = "admin_credit"
OP_ADMIN_DEBIT       = "admin_debit"
OP_RECHARGE_DEBIT    = "recharge_debit"     # Phase 3: distributor-funded recharge
# Reserved for future phases:
# OP_COMMISSION_CREDIT = "commission_credit"
# OP_REFUND_CREDIT     = "refund_credit"
# OP_CASHBACK_CREDIT   = "cashback_credit"
# OP_BONUS_CREDIT      = "bonus_credit"

# created_source constants
SOURCE_TELEGRAM_ADMIN = "telegram_admin"
SOURCE_SYSTEM         = "system"
SOURCE_RECHARGE       = "recharge"          # Phase 3: auto-debit on recharge success
SOURCE_SCHEDULER      = "scheduler"
SOURCE_API            = "api"
SOURCE_MIGRATION      = "migration"

# Sentinel value for created_by when the operation is system-initiated
SYSTEM_ACTOR = 0


class DistributorWalletService:
    """
    Atomic wallet operations for distributors.

    Public interface:
      credit(distributor_id, amount, created_by, ...)  → ledger entry dict
      debit(distributor_id, amount, created_by, ...)   → ledger entry dict
      get_balance(distributor_id)                       → float  (O(1) cache read)
      get_ledger(distributor_id, limit, offset)         → List[dict]
      count_ledger(distributor_id)                      → int
      get_ledger_entry(entry_id)                        → dict | None

    There is no set_balance() method — balance is exclusively a side-effect
    of credit() or debit().
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def credit(
        self,
        distributor_id: int,
        amount: float,
        created_by: int,
        reference_type: Optional[str] = None,
        reference_value: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        created_source: str = SOURCE_TELEGRAM_ADMIN,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Credit (add funds) to a distributor's wallet.
        amount must be > 0.  Distributor must be active.
        Returns the new ledger entry.
        Raises ValueError with a key string on any validation failure.
        """
        entry = await self._db.distributor_wallet_credit(
            distributor_id=distributor_id,
            amount=amount,
            created_by=created_by,
            created_source=created_source,
            reference_type=reference_type,
            reference_value=reference_value,
            idempotency_key=idempotency_key,
            notes=notes,
        )
        await self._db.log(
            "distributor_wallet_credit",
            f"distributor_id={distributor_id} amount={amount} entry_id={entry['id']} by={created_by}",
            level="INFO",
        )
        return entry

    async def debit(
        self,
        distributor_id: int,
        amount: float,
        created_by: int,
        reference_type: Optional[str] = None,
        reference_value: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        created_source: str = SOURCE_TELEGRAM_ADMIN,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Debit (remove funds) from a distributor's wallet.
        amount must be > 0 and <= current balance.
        Raises ValueError with a key string on any validation failure.
        """
        entry = await self._db.distributor_wallet_debit(
            distributor_id=distributor_id,
            amount=amount,
            created_by=created_by,
            created_source=created_source,
            reference_type=reference_type,
            reference_value=reference_value,
            idempotency_key=idempotency_key,
            notes=notes,
        )
        await self._db.log(
            "distributor_wallet_debit",
            f"distributor_id={distributor_id} amount={amount} entry_id={entry['id']} by={created_by}",
            level="INFO",
        )
        return entry

    async def get_balance(self, distributor_id: int) -> Optional[float]:
        """
        O(1) runtime balance read — always uses distributors.wallet_balance.
        Returns None if the distributor does not exist.
        Never queries distributor_ledger.
        """
        d = await self._db.get_distributor(distributor_id)
        return d["wallet_balance"] if d else None

    async def get_ledger(
        self, distributor_id: int, limit: int = 8, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Newest-first page of ledger entries.  Read-only, never cached."""
        return await self._db.get_distributor_ledger(distributor_id, limit, offset)

    async def count_ledger(self, distributor_id: int) -> int:
        return await self._db.count_distributor_ledger(distributor_id)

    async def get_ledger_entry(self, entry_id: int) -> Optional[Dict[str, Any]]:
        return await self._db.get_distributor_ledger_entry(entry_id)


# ---------------------------------------------------------------------------
# Phase 3 — Distributor-funded Recharge & Activy
#
# ACCOUNTING CONTRACT:
#   • Balance is checked upfront as a fast-fail only (O(1) cache read).
#   • No transaction row is created if the balance check fails.
#   • OneClick is called ONLY after a transaction row exists.
#   • Wallet deduction (debit) happens ONLY after confirmed OneClick success.
#   • debit() is the authoritative guard — it is atomic and enforces the
#     balance >= 0 constraint at DB level even under concurrent admin debits.
#   • If debit() raises after OneClick success (rare race), the transaction
#     is marked "failed" and a CRITICAL log is emitted for reconciliation.
#     The real top-up occurred but the wallet was not charged; an admin must
#     resolve manually.
#   • On any failure path: no wallet deduction, no ledger entry, ever.
#
# DistributorRechargeService never touches RechargeService or WalletService.
# RechargeService is left completely unchanged.
# ---------------------------------------------------------------------------

class DistributorRechargeService:
    """
    Distributor-funded recharge and Activy activation (Phase 3).

    Composes:
      - OneClickAPI:  the real provider call (unchanged from customer flow)
      - Database:     create_transaction / update_transaction for audit records
      - DistributorWalletService.debit(): atomic post-success wallet deduction
        with append-only ledger entry (Phase 2)

    Public interface:
      process_standard(distributor_id, db_user_id, phone, amount, operator) → Dict
      process_activy(distributor_id, db_user_id, phone, plan_code, plan_name, amount) → Dict

    Result dicts mirror RechargeService.process_standard/process_activy exactly
    so handlers can reuse the same render callbacks with minimal changes.
    """

    def __init__(
        self,
        db: Database,
        api: Any,
        dist_wallet_service: DistributorWalletService,
    ) -> None:
        self._db = db
        self._api = api
        self._dws = dist_wallet_service

    async def _verify_active_with_balance(
        self, distributor_id: int, amount: float
    ) -> Optional[Dict[str, Any]]:
        """
        Re-verify active status and check balance (fast-fail).
        Returns an error dict on failure, None when the check passes.
        This is an optimisation only — debit() is the authoritative guard.
        """
        dist = await self._db.get_distributor(distributor_id)
        if not dist:
            return {"success": False, "reason": "distributor_not_found"}
        if dist["status"] != STATUS_ACTIVE:
            return {"success": False, "reason": "distributor_suspended"}
        if dist["wallet_balance"] < amount:
            return {
                "success": False,
                "reason": "insufficient_balance",
                "balance": dist["wallet_balance"],
                "required": amount,
            }
        return None

    async def process_standard(
        self,
        distributor_id: int,
        db_user_id: int,
        phone: str,
        amount: int,
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Standard prepaid top-up funded from the distributor's wallet.

        Mirrors RechargeService.process_standard in structure but reads/writes
        the distributor wallet instead of the customer balances table.
        OneClickAPI.recharge_standard is called unchanged.
        """
        from services import APIError, NetworkError, OperatorDetector

        err = await self._verify_active_with_balance(distributor_id, float(amount))
        if err:
            return err

        if not operator:
            operator = OperatorDetector.detect(phone)

        tx_id = await self._db.create_transaction(
            user_id=db_user_id,
            tx_type="standard",
            amount=amount,
            description=f"Recharge {phone}",
            phone=phone,
            status="pending",
            operator=operator,
        )

        try:
            result = await self._api.recharge_standard(phone, amount, operator)
        except (NetworkError, APIError) as exc:
            logger.error(
                "DistributorRechargeService: standard API error — "
                "dist_id=%s phone=%s error=%s",
                distributor_id, phone, exc,
            )
            await self._db.update_transaction(tx_id, "failed", str(exc))
            await self._db.log(
                "dist_recharge_api_error",
                f"distributor_id={distributor_id} phone={phone} error={exc}",
                db_user_id,
                level="ERROR",
            )
            return {"success": False, "reason": "api_error"}

        if not result.success:
            await self._db.update_transaction(tx_id, "failed", str(result.raw))
            await self._db.log(
                "dist_recharge_failed",
                f"distributor_id={distributor_id} phone={phone} amount={amount} "
                f"code={result.code}",
                db_user_id,
                level="WARNING",
            )
            return {"success": False, "reason": "provider_error", "message": result.message}

        # OneClick confirmed success — debit BEFORE update_transaction so the
        # ledger entry is created only when the deduction itself succeeded.
        try:
            await self._dws.debit(
                distributor_id=distributor_id,
                amount=float(amount),
                created_by=SYSTEM_ACTOR,
                created_source=SOURCE_RECHARGE,
                reference_type="transaction",
                reference_value=str(tx_id),
                notes=f"Standard recharge {phone} {amount} DZD",
            )
        except ValueError as exc:
            # Extremely rare: balance changed between pre-flight and debit.
            # OneClick already succeeded — real top-up occurred but wallet was
            # not charged. Marked failed here for user visibility; admin must
            # reconcile from the CRITICAL log.
            logger.critical(
                "DistributorRechargeService: debit FAILED after OneClick success — "
                "dist_id=%s tx_id=%s phone=%s amount=%s ref=%s error=%s — "
                "REQUIRES MANUAL RECONCILIATION",
                distributor_id, tx_id, phone, amount, result.reference, exc,
            )
            await self._db.update_transaction(
                tx_id, "failed", f"post_success_debit_failed:{exc}"
            )
            await self._db.log(
                "dist_recharge_debit_failed_post_success",
                f"distributor_id={distributor_id} tx_id={tx_id} phone={phone} "
                f"amount={amount} ref={result.reference} error={exc}",
                db_user_id,
                level="ERROR",
            )
            return {"success": False, "reason": "debit_failed"}

        await self._db.update_transaction(tx_id, "success", str(result.raw), result.reference)
        await self._db.log(
            "dist_standard_recharge",
            f"distributor_id={distributor_id} phone={phone} amount={amount} "
            f"operator={operator} ref={result.reference}",
            db_user_id,
        )
        logger.info(
            "DistributorRechargeService: standard success — "
            "dist_id=%s tx_id=%s phone=%s amount=%s operator=%s ref=%s",
            distributor_id, tx_id, phone, amount, operator, result.reference,
        )
        return {
            "success": True,
            "phone": phone,
            "amount": amount,
            "operator": operator,
            "reference": result.reference,
        }

    async def process_activy(
        self,
        distributor_id: int,
        db_user_id: int,
        phone: str,
        plan_code: str,
        plan_name: str,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Activy fixed-plan activation funded from the distributor's wallet.

        Mirrors RechargeService.process_activy in structure.
        OneClickAPI.recharge_activy is called unchanged.
        """
        from services import APIError, NetworkError

        price = float(amount)

        err = await self._verify_active_with_balance(distributor_id, price)
        if err:
            return err

        tx_id = await self._db.create_transaction(
            user_id=db_user_id,
            tx_type="activy",
            amount=price,
            description=f"Activy {plan_name} → {phone}",
            phone=phone,
            status="processing",
        )

        try:
            result = await self._api.recharge_activy(phone, plan_code, price)
        except (NetworkError, APIError) as exc:
            logger.error(
                "DistributorRechargeService: activy API error — "
                "dist_id=%s phone=%s error=%s",
                distributor_id, phone, exc,
            )
            await self._db.update_transaction(tx_id, "failed", str(exc))
            return {"success": False, "reason": "api_error"}

        if not result.success:
            await self._db.update_transaction(tx_id, "failed", str(result.raw))
            await self._db.log(
                "dist_activy_failed",
                f"distributor_id={distributor_id} phone={phone} offer={plan_code} "
                f"code={result.code}",
                db_user_id,
                level="WARNING",
            )
            return {"success": False, "reason": "provider_error", "message": result.message}

        try:
            await self._dws.debit(
                distributor_id=distributor_id,
                amount=price,
                created_by=SYSTEM_ACTOR,
                created_source=SOURCE_RECHARGE,
                reference_type="transaction",
                reference_value=str(tx_id),
                notes=f"Activy {plan_name} {phone} {price} DZD",
            )
        except ValueError as exc:
            logger.critical(
                "DistributorRechargeService: activy debit FAILED after OneClick success — "
                "dist_id=%s tx_id=%s phone=%s offer=%s ref=%s error=%s — "
                "REQUIRES MANUAL RECONCILIATION",
                distributor_id, tx_id, phone, plan_code, result.reference, exc,
            )
            await self._db.update_transaction(
                tx_id, "failed", f"post_success_debit_failed:{exc}"
            )
            await self._db.log(
                "dist_activy_debit_failed_post_success",
                f"distributor_id={distributor_id} tx_id={tx_id} phone={phone} "
                f"offer={plan_code} ref={result.reference} error={exc}",
                db_user_id,
                level="ERROR",
            )
            return {"success": False, "reason": "debit_failed"}

        await self._db.update_transaction(tx_id, "success", str(result.raw), result.reference)
        await self._db.log(
            "dist_activy_recharge",
            f"distributor_id={distributor_id} phone={phone} offer={plan_code} "
            f"ref={result.reference}",
            db_user_id,
        )
        logger.info(
            "DistributorRechargeService: activy success — "
            "dist_id=%s tx_id=%s phone=%s offer=%s ref=%s",
            distributor_id, tx_id, phone, plan_code, result.reference,
        )
        return {
            "success": True,
            "phone": phone,
            "offer": {
                "name_en": plan_name,
                "name_ar": plan_name,
                "price": price,
                "code": plan_code,
            },
            "reference": result.reference,
        }
