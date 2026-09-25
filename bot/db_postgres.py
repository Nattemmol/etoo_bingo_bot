"""PostgreSQL database backend using ``asyncpg``.

Drop-in replacement for :pymod:`bot.database` (aiosqlite).  Every public
function has the **exact same** name, parameters and return type; only the
internals use ``asyncpg`` instead of ``aiosqlite``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg

from bot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection pool (singleton)
# ---------------------------------------------------------------------------

_pool: asyncpg.Pool | None = None

_MIGRATIONS_FILE = Path(__file__).resolve().parent.parent / "migrations" / "001_init_schema.sql"


async def _get_pool() -> asyncpg.Pool:
    """Return the module-level connection pool, raising if uninitialised."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    return _pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_to_dict(record: asyncpg.Record | None) -> dict | None:
    """Convert an ``asyncpg.Record`` to a plain ``dict``, or ``None``."""
    return dict(record) if record is not None else None


def _numeric(value: Any) -> float:
    """Safely coerce a DB value (``Decimal`` / ``None``) to ``float``."""
    if value is None:
        return 0.0
    return float(value)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create the asyncpg pool and run the schema DDL migration."""
    global _pool

    dsn = getattr(settings, "database_url", "")
    if not dsn:
        raise ValueError(
            "settings.database_url is not configured. "
            "Set DATABASE_URL in your environment."
        )

    _pool = await asyncpg.create_pool(dsn=dsn, min_size=5, max_size=20)
    logger.info("asyncpg pool created (min=5, max=20)")

    # Run the schema migration
    if _MIGRATIONS_FILE.exists():
        schema_sql = _MIGRATIONS_FILE.read_text(encoding="utf-8")
        async with _pool.acquire() as conn:
            await conn.execute(schema_sql)
        logger.info("Schema migration %s applied.", _MIGRATIONS_FILE.name)
    else:
        logger.warning("Migration file %s not found — skipping DDL.", _MIGRATIONS_FILE)

    logger.info("PostgreSQL database initialised.")


async def close_db() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            logger.exception("Error closing asyncpg pool")
        _pool = None
        logger.info("asyncpg pool closed.")


async def ping_db() -> bool:
    """Return ``True`` if the database is reachable."""
    pool = await _get_pool()
    try:
        row = await pool.fetchval("SELECT 1")
        return row is not None
    except Exception:
        logger.exception("ping_db failed")
        return False


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

async def get_user(telegram_id: int) -> dict | None:
    """Fetch a user by Telegram ID."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM users WHERE telegram_id = $1", telegram_id
    )
    return _record_to_dict(row)


async def create_user(
    telegram_id: int,
    phone_number: str,
    username: str | None,
    first_name: str | None,
) -> dict:
    """Create or update a user (upsert) and return the resulting row."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO users (telegram_id, phone_number, username, first_name)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (telegram_id) DO UPDATE SET
            phone_number = EXCLUDED.phone_number,
            username     = COALESCE(EXCLUDED.username, users.username),
            first_name   = COALESCE(EXCLUDED.first_name, users.first_name),
            updated_at   = NOW()
        """,
        telegram_id, phone_number, username, first_name,
    )
    user = await get_user(telegram_id)
    assert user is not None
    return user


async def ensure_user(
    telegram_id: int,
    phone_number: str = "+251900000000",
    username: str | None = None,
    first_name: str | None = None,
    balance: float | None = None,
) -> dict:
    """Ensure a user exists (create if missing) and optionally set balance."""
    user = await create_user(telegram_id, phone_number, username, first_name)
    if balance is not None:
        await update_balance(telegram_id, balance)
        user["balance"] = balance
    return user


async def get_balance(telegram_id: int) -> float:
    """Return the current balance for *telegram_id*, or 0.0 if not found."""
    user = await get_user(telegram_id)
    return _numeric(user["balance"]) if user else 0.0


async def update_balance(telegram_id: int, new_balance: float) -> None:
    """Set the balance for *telegram_id* to *new_balance*."""
    pool = await _get_pool()
    await pool.execute(
        "UPDATE users SET balance = $1, updated_at = NOW() WHERE telegram_id = $2",
        new_balance, telegram_id,
    )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

async def add_transaction(
    telegram_id: int,
    tx_type: str,
    amount: float,
    status: str = "completed",
    description: str | None = None,
) -> None:
    """Insert a new transaction record."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO transactions (telegram_id, type, amount, status, description)
        VALUES ($1, $2, $3, $4, $5)
        """,
        telegram_id, tx_type, amount, status, description,
    )


async def get_transactions(telegram_id: int, limit: int = 20) -> list[dict]:
    """Return the most recent *limit* transactions for a user."""
    pool = await _get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM transactions
        WHERE telegram_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        telegram_id, limit,
    )
    return [dict(r) for r in rows]


async def deduct_balance(
    telegram_id: int, amount: float, description: str,
) -> tuple[bool, float]:
    """Atomically deduct *amount* if sufficient balance.

    Returns ``(success, new_balance)``.  Uses a single ``UPDATE … WHERE
    balance >= amount RETURNING`` inside a transaction — no application-level
    locks required.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance    = balance - $1,
                    updated_at = NOW()
                WHERE telegram_id = $2
                  AND balance >= $1
                RETURNING balance
                """,
                amount, telegram_id,
            )
            if row is None:
                # Either the user doesn't exist or insufficient funds.
                bal_row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1",
                    telegram_id,
                )
                return False, _numeric(bal_row["balance"]) if bal_row else 0.0

            new_balance = _numeric(row["balance"])
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description)
                VALUES ($1, 'game_entry', $2, 'completed', $3)
                """,
                telegram_id, amount, description,
            )
            return True, round(new_balance, 2)


async def credit_balance(
    telegram_id: int, amount: float, description: str,
) -> float:
    """Credit winnings to user balance atomically.

    Returns the new balance.  Both the ``UPDATE`` and the transaction record
    are committed together.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance    = balance + $1,
                    updated_at = NOW()
                WHERE telegram_id = $2
                RETURNING balance
                """,
                amount, telegram_id,
            )
            if row is None:
                return 0.0

            new_balance = _numeric(row["balance"])
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description)
                VALUES ($1, 'win', $2, 'completed', $3)
                """,
                telegram_id, amount, description,
            )
            return round(new_balance, 2)


# ---------------------------------------------------------------------------
# Deposit helpers (SMS / auto-credit)
# ---------------------------------------------------------------------------

async def get_deposit_by_fingerprint(fingerprint: str) -> dict | None:
    """Return an existing completed deposit carrying the same SMS fingerprint."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM transactions
        WHERE type = 'deposit' AND fingerprint = $1 AND status = 'completed'
        LIMIT 1
        """,
        fingerprint,
    )
    return _record_to_dict(row)


async def auto_credit_deposit(
    telegram_id: int,
    amount: float,
    fingerprint: str,
    description: str,
) -> tuple[bool, float, bool]:
    """Credit a verified deposit atomically using ``SELECT FOR UPDATE``.

    Returns ``(credited, new_balance, already_used)``.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the user row to prevent concurrent credits.
            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                telegram_id,
            )
            if not user_row:
                return False, 0.0, False

            # Check for duplicate fingerprint.
            existing = await conn.fetchrow(
                """
                SELECT id FROM transactions
                WHERE type = 'deposit' AND fingerprint = $1 AND status = 'completed'
                LIMIT 1
                """,
                fingerprint,
            )
            if existing:
                return False, _numeric(user_row["balance"]), True

            new_balance = round(_numeric(user_row["balance"]) + amount, 2)
            await conn.execute(
                "UPDATE users SET balance = $1, updated_at = NOW() WHERE telegram_id = $2",
                new_balance, telegram_id,
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description, fingerprint)
                VALUES ($1, 'deposit', $2, 'completed', $3, $4)
                """,
                telegram_id, amount, description, fingerprint,
            )
            return True, new_balance, False


# ---------------------------------------------------------------------------
# Telebirr order helpers
# ---------------------------------------------------------------------------

async def create_telebirr_order(
    merch_order_id: str,
    telegram_id: int,
    amount: float,
    checkout_url: str,
) -> None:
    """Record a new pending Telebirr order."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO telebirr_orders
            (merch_order_id, telegram_id, amount, status, checkout_url)
        VALUES ($1, $2, $3, 'pending', $4)
        ON CONFLICT (merch_order_id) DO NOTHING
        """,
        merch_order_id, telegram_id, amount, checkout_url,
    )


async def get_telebirr_order(merch_order_id: str) -> dict | None:
    """Fetch a Telebirr order record by merchant order ID."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM telebirr_orders WHERE merch_order_id = $1",
        merch_order_id,
    )
    return _record_to_dict(row)


async def complete_telebirr_order(
    merch_order_id: str,
) -> tuple[bool, float, int]:
    """Idempotently mark a Telebirr order as completed and credit the user.

    Returns ``(credited, new_balance, telegram_id)``.
    ``credited=False`` if the order was already completed or not found.
    Entire operation is atomic.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT * FROM telebirr_orders WHERE merch_order_id = $1 FOR UPDATE",
                merch_order_id,
            )
            if not order:
                return False, 0.0, 0

            tid: int = order["telegram_id"]
            amt: float = _numeric(order["amount"])

            if order["status"] != "pending":
                user_row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1",
                    tid,
                )
                return False, _numeric(user_row["balance"]) if user_row else 0.0, tid

            # Lock the user row & credit.
            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                tid,
            )
            if not user_row:
                return False, 0.0, tid

            new_balance = round(_numeric(user_row["balance"]) + amt, 2)

            await conn.execute(
                "UPDATE telebirr_orders SET status = 'completed' WHERE merch_order_id = $1",
                merch_order_id,
            )
            await conn.execute(
                "UPDATE users SET balance = $1, updated_at = NOW() WHERE telegram_id = $2",
                new_balance, tid,
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description)
                VALUES ($1, 'deposit', $2, 'completed', $3)
                """,
                tid, amt, f"Telebirr deposit — order {merch_order_id}",
            )
            return True, new_balance, tid


# ---------------------------------------------------------------------------
# PeerPay webhook helpers
# ---------------------------------------------------------------------------

async def record_webhook_event_once(
    event_id: str,
    delivery_id: str,
    event_type: str,
    object_id: str,
    payload: str,
) -> bool:
    """Deduplicate PeerPay deliveries by event id.

    Uses ``INSERT … ON CONFLICT DO NOTHING`` and checks the command tag to
    determine whether this was a new insertion.
    """
    pool = await _get_pool()
    result = await pool.execute(
        """
        INSERT INTO peerpay_events
            (event_id, delivery_id, event_type, object_id, payload)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (event_id) DO NOTHING
        """,
        event_id, delivery_id, event_type, object_id, payload,
    )
    # asyncpg returns a command tag like "INSERT 0 1" or "INSERT 0 0".
    return result.endswith(" 1")


async def get_webhook_event(event_id: str) -> dict | None:
    """Return a webhook event by its ``event_id``."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM peerpay_events WHERE event_id = $1",
        event_id,
    )
    return _record_to_dict(row)


async def upsert_peerpay_deposit(
    payment_id: str,
    telegram_id: int,
    amount: float,
    currency: str,
    merchant_order_id: str | None,
    status: str,
) -> None:
    """Record / refresh a PeerPay deposit row from a status event."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO peerpay_deposits
            (payment_id, telegram_id, merchant_order_id, status, amount, currency)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (payment_id) DO UPDATE SET
            status     = EXCLUDED.status,
            updated_at = NOW()
        """,
        payment_id, telegram_id, merchant_order_id, status, amount, currency,
    )


async def get_peerpay_deposit(payment_id_or_ref: str) -> dict | None:
    """Find a PeerPay deposit by ``payment_id`` *or* ``merchant_order_id``."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM peerpay_deposits
        WHERE payment_id = $1 OR merchant_order_id = $1
        LIMIT 1
        """,
        payment_id_or_ref,
    )
    return _record_to_dict(row)


async def credit_peerpay_deposit_once(
    payment_id: str,
    telegram_id: int,
    amount: float,
    merchant_order_id: str | None = None,
) -> tuple[bool, float, bool]:
    """Idempotently credit a confirmed PeerPay deposit.

    Returns ``(credited, new_balance, already_credited)``.
    Entire operation is atomic with ``SELECT FOR UPDATE`` on the deposit row.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Ensure the deposit row exists.
            await conn.execute(
                """
                INSERT INTO peerpay_deposits
                    (payment_id, telegram_id, merchant_order_id, status, amount)
                VALUES ($1, $2, $3, 'succeeded', $4)
                ON CONFLICT (payment_id) DO NOTHING
                """,
                payment_id, telegram_id, merchant_order_id, amount,
            )

            dep = await conn.fetchrow(
                """
                SELECT telegram_id, credited
                FROM peerpay_deposits
                WHERE payment_id = $1
                FOR UPDATE
                """,
                payment_id,
            )
            if not dep:
                return False, 0.0, False

            target_id = int(dep["telegram_id"])
            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                target_id,
            )
            if not user_row:
                return False, 0.0, False

            current_balance = _numeric(user_row["balance"])
            if dep["credited"]:
                return False, current_balance, True

            new_balance = round(current_balance + amount, 2)
            fingerprint = f"peerpay_dep:{payment_id}"

            await conn.execute(
                "UPDATE users SET balance = $1, updated_at = NOW() WHERE telegram_id = $2",
                new_balance, target_id,
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description, fingerprint)
                VALUES ($1, 'deposit', $2, 'completed', $3, $4)
                """,
                target_id, amount, f"PeerPay deposit — {payment_id}", fingerprint,
            )
            await conn.execute(
                """
                UPDATE peerpay_deposits
                SET credited = TRUE, status = 'succeeded', updated_at = NOW()
                WHERE payment_id = $1
                """,
                payment_id,
            )
            return True, new_balance, False


# ---------------------------------------------------------------------------
# PeerPay withdrawal helpers
# ---------------------------------------------------------------------------

async def create_peerpay_withdrawal_hold(
    payment_id: str,
    telegram_id: int,
    amount: float,
    status: str = "created",
    decision_code: str | None = None,
) -> None:
    """Record a PeerPay withdrawal hold after withdrawal creation."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO peerpay_withdrawals
            (payment_id, telegram_id, amount, status, decision_code)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (payment_id) DO NOTHING
        """,
        payment_id, telegram_id, amount, status, decision_code,
    )


async def get_peerpay_withdrawal(payment_id: str) -> dict | None:
    """Fetch a PeerPay withdrawal row by ``payment_id``."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM peerpay_withdrawals WHERE payment_id = $1",
        payment_id,
    )
    return _record_to_dict(row)


async def update_peerpay_withdrawal_progress(
    payment_id: str,
    status: str,
    verification_status: str | None = None,
    decision_code: str | None = None,
) -> None:
    """Persist non-wallet-mutating withdrawal events (the hold is unchanged)."""
    pool = await _get_pool()
    await pool.execute(
        """
        UPDATE peerpay_withdrawals
        SET status        = $1,
            decision_code = CASE WHEN $2::TEXT IS NULL THEN decision_code ELSE $2 END,
            updated_at    = NOW()
        WHERE payment_id = $3
        """,
        status, decision_code, payment_id,
    )


async def capture_peerpay_withdrawal_once(payment_id: str) -> tuple[bool, int]:
    """Capture the wallet hold for a completed withdrawal — exactly once.

    Returns ``(captured, telegram_id)``.
    Uses ``SELECT FOR UPDATE`` to prevent races.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT telegram_id, captured, released
                FROM peerpay_withdrawals
                WHERE payment_id = $1
                FOR UPDATE
                """,
                payment_id,
            )
            if not row:
                return False, 0
            if row["captured"] or row["released"]:
                return False, int(row["telegram_id"])

            await conn.execute(
                """
                UPDATE peerpay_withdrawals
                SET captured    = TRUE,
                    hold_status = 'captured',
                    status      = 'succeeded',
                    updated_at  = NOW()
                WHERE payment_id = $1
                  AND captured = FALSE
                  AND released = FALSE
                """,
                payment_id,
            )
            return True, int(row["telegram_id"])


async def release_peerpay_withdrawal_once(payment_id: str) -> tuple[bool, float]:
    """Release the hold for a failed/expired/cancelled withdrawal — exactly once.

    Returns ``(released, new_balance)``.
    Uses ``SELECT FOR UPDATE`` on the withdrawal row.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT telegram_id, amount, captured, released
                FROM peerpay_withdrawals
                WHERE payment_id = $1
                FOR UPDATE
                """,
                payment_id,
            )
            if not row or row["captured"] or row["released"]:
                return False, 0.0

            target_id = int(row["telegram_id"])
            amount = _numeric(row["amount"])

            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                target_id,
            )
            if not user_row:
                return False, 0.0

            new_balance = round(_numeric(user_row["balance"]) + amount, 2)
            fingerprint = f"peerpay_wd_refund:{payment_id}"

            await conn.execute(
                "UPDATE users SET balance = $1, updated_at = NOW() WHERE telegram_id = $2",
                new_balance, target_id,
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (telegram_id, type, amount, status, description, fingerprint)
                VALUES ($1, 'deposit', $2, 'completed', $3, $4)
                """,
                target_id, amount, f"PeerPay withdrawal refund — {payment_id}", fingerprint,
            )
            await conn.execute(
                """
                UPDATE peerpay_withdrawals
                SET released    = TRUE,
                    hold_status = 'released',
                    status      = 'released',
                    updated_at  = NOW()
                WHERE payment_id = $1
                  AND captured = FALSE
                  AND released = FALSE
                """,
                payment_id,
            )
            return True, new_balance


# ---------------------------------------------------------------------------
# House revenue
# ---------------------------------------------------------------------------

async def record_house_revenue(
    room_id: str,
    cards_count: int,
    cut_per_card: float,
    total_revenue: float,
) -> None:
    """Record house commission earnings from a completed bingo round."""
    pool = await _get_pool()
    await pool.execute(
        """
        INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue)
        VALUES ($1, $2, $3, $4)
        """,
        room_id, cards_count, cut_per_card, total_revenue,
    )


async def get_house_revenue_summary() -> dict:
    """Return total income and per-room totals for house revenue."""
    pool = await _get_pool()
    rows = await pool.fetch(
        """
        SELECT room_id,
               COUNT(*)           AS rounds,
               SUM(cards_count)   AS total_cards,
               SUM(total_revenue) AS total_revenue
        FROM house_revenue
        GROUP BY room_id
        """
    )
    by_room = {r["room_id"]: dict(r) for r in rows}
    total_row = await pool.fetchval("SELECT SUM(total_revenue) FROM house_revenue")
    total = _numeric(total_row)
    return {"total_revenue": total, "by_room": by_room}


# ---------------------------------------------------------------------------
# Active round persistence (crash recovery)
# ---------------------------------------------------------------------------

async def save_active_round(
    room_id: str,
    phase: str,
    pot: float,
    house_income: float,
    called_numbers: list[int],
    taken_cards: dict[int, int],
    player_data: dict,
) -> None:
    """Persist current round state for crash recovery (upsert)."""
    pool = await _get_pool()

    # Convert int keys → string keys for JSON storage (same as SQLite version).
    called_json = json.dumps(called_numbers)
    taken_json = json.dumps({str(k): v for k, v in taken_cards.items()})
    player_json = json.dumps(player_data)

    await pool.execute(
        """
        INSERT INTO active_rounds
            (room_id, phase, pot, house_income,
             called_numbers, taken_cards, player_data, updated_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, NOW())
        ON CONFLICT (room_id) DO UPDATE SET
            phase          = EXCLUDED.phase,
            pot            = EXCLUDED.pot,
            house_income   = EXCLUDED.house_income,
            called_numbers = EXCLUDED.called_numbers,
            taken_cards    = EXCLUDED.taken_cards,
            player_data    = EXCLUDED.player_data,
            updated_at     = NOW()
        """,
        room_id, phase, pot, house_income,
        called_json, taken_json, player_json,
    )


async def get_active_round(room_id: str) -> dict | None:
    """Load a persisted round for crash recovery.

    JSONB columns come back as native Python dicts/lists from asyncpg.
    ``taken_cards`` keys are converted back to ``int``.
    """
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM active_rounds WHERE room_id = $1",
        room_id,
    )
    if not row:
        return None

    d = dict(row)

    # called_numbers: already a list from JSONB
    cn = d.get("called_numbers")
    if isinstance(cn, str):
        cn = json.loads(cn)
    d["called_numbers"] = cn if cn is not None else []

    # taken_cards: convert string keys → int keys
    tc = d.get("taken_cards")
    if isinstance(tc, str):
        tc = json.loads(tc)
    d["taken_cards"] = {int(k): v for k, v in (tc or {}).items()}

    # player_data: already a dict from JSONB
    pd_ = d.get("player_data")
    if isinstance(pd_, str):
        pd_ = json.loads(pd_)
    d["player_data"] = pd_ if pd_ is not None else {}

    return d


async def clear_active_round(room_id: str) -> None:
    """Remove persisted round state after a round completes normally."""
    pool = await _get_pool()
    await pool.execute(
        "DELETE FROM active_rounds WHERE room_id = $1",
        room_id,
    )


# ---------------------------------------------------------------------------
# Game rounds (NEW — historical record of completed rounds)
# ---------------------------------------------------------------------------

async def record_game_round(
    room_id: str,
    phase: str,
    entry_fee: float,
    house_cut_rate: float,
    total_cards_sold: int,
    pot: float,
    house_income: float,
    called_numbers: list[int],
    winners: list[dict],
) -> str:
    """Record a completed game round and return the generated ``round_id`` UUID.

    Parameters
    ----------
    room_id:
        Identifier of the bingo room.
    phase:
        Final phase the round ended in (e.g. ``'finished'``).
    entry_fee:
        Per-card entry fee.
    house_cut_rate:
        House commission rate (e.g. ``0.10`` for 10 %).
    total_cards_sold:
        Number of cards sold in this round.
    pot:
        Total prize pot.
    house_income:
        House revenue collected.
    called_numbers:
        Ordered list of bingo numbers called.
    winners:
        List of winner dicts (e.g. ``[{"telegram_id": ..., "prize": ...}]``).

    Returns
    -------
    str
        The UUID of the newly inserted ``game_rounds`` row.
    """
    pool = await _get_pool()
    called_json = json.dumps(called_numbers)
    winners_json = json.dumps(winners)

    round_id = await pool.fetchval(
        """
        INSERT INTO game_rounds
            (room_id, phase, entry_fee, house_cut_rate, total_cards_sold,
             pot, house_income, called_numbers, winners, ended_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, NOW())
        RETURNING round_id
        """,
        room_id, phase, entry_fee, house_cut_rate, total_cards_sold,
        pot, house_income, called_json, winners_json,
    )
    logger.info("Recorded game round %s for room %s", round_id, room_id)
    return str(round_id)
