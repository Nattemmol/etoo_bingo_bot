import aiosqlite

from bot.config import settings

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    phone_number TEXT NOT NULL,
    username TEXT,
    first_name TEXT,
    balance REAL NOT NULL DEFAULT 0.0,
    registered_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
);
"""

CREATE_TELEBIRR_ORDERS = """
CREATE TABLE IF NOT EXISTS telebirr_orders (
    merch_order_id TEXT PRIMARY KEY,
    telegram_id    INTEGER NOT NULL,
    amount         REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    checkout_url   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
);
"""

CREATE_PEERPAY_EVENTS = """
CREATE TABLE IF NOT EXISTS peerpay_events (
    event_id     TEXT PRIMARY KEY,
    delivery_id  TEXT,
    event_type   TEXT NOT NULL,
    object_id    TEXT,
    payload      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_PEERPAY_DEPOSITS = """
CREATE TABLE IF NOT EXISTS peerpay_deposits (
    payment_id        TEXT PRIMARY KEY,
    telegram_id       INTEGER NOT NULL,
    merchant_order_id TEXT,
    status            TEXT NOT NULL DEFAULT 'created',
    amount            REAL NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL DEFAULT 'ETB',
    credited          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_PEERPAY_WITHDRAWALS = """
CREATE TABLE IF NOT EXISTS peerpay_withdrawals (
    payment_id    TEXT PRIMARY KEY,
    telegram_id   INTEGER NOT NULL,
    amount        REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'created',
    hold_status   TEXT NOT NULL DEFAULT 'held',
    captured      INTEGER NOT NULL DEFAULT 0,
    released      INTEGER NOT NULL DEFAULT 0,
    decision_code TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_HOUSE_REVENUE = """
CREATE TABLE IF NOT EXISTS house_revenue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id        TEXT NOT NULL,
    cards_count    INTEGER NOT NULL,
    cut_per_card   REAL NOT NULL,
    total_revenue  REAL NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def _ensure_transaction_fingerprint(db) -> None:
    """Add the fingerprint column (dedup key for auto-approved deposits)."""
    async with db.execute("PRAGMA table_info(transactions)") as cursor:
        columns = await cursor.fetchall()
    if not any(row[1] == "fingerprint" for row in columns):
        await db.execute(
            "ALTER TABLE transactions ADD COLUMN fingerprint TEXT"
        )
        await db.commit()


async def init_db() -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(CREATE_USERS)
        await db.execute(CREATE_TRANSACTIONS)
        await db.execute(CREATE_TELEBIRR_ORDERS)
        await db.execute(CREATE_PEERPAY_EVENTS)
        await db.execute(CREATE_PEERPAY_DEPOSITS)
        await db.execute(CREATE_PEERPAY_WITHDRAWALS)
        await db.execute(CREATE_HOUSE_REVENUE)
        await _ensure_transaction_fingerprint(db)
        await db.commit()


async def ping_db() -> bool:
    async with aiosqlite.connect(settings.database_path) as db:
        async with db.execute("SELECT 1") as cursor:
            row = await cursor.fetchone()
            return row is not None


async def get_user(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_user(
    telegram_id: int,
    phone_number: str,
    username: str | None,
    first_name: str | None,
) -> dict:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO users (telegram_id, phone_number, username, first_name)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, phone_number, username, first_name),
        )
        await db.commit()

    user = await get_user(telegram_id)
    assert user is not None
    return user


async def get_balance(telegram_id: int) -> float:
    user = await get_user(telegram_id)
    return float(user["balance"]) if user else 0.0


async def update_balance(telegram_id: int, new_balance: float) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?",
            (new_balance, telegram_id),
        )
        await db.commit()


async def add_transaction(
    telegram_id: int,
    tx_type: str,
    amount: float,
    status: str = "completed",
    description: str | None = None,
) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, status, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, tx_type, amount, status, description),
        )
        await db.commit()


async def get_transactions(telegram_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM transactions
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def deduct_balance(
    telegram_id: int, amount: float, description: str
) -> tuple[bool, float]:
    """Deduct amount if sufficient balance. Returns (success, new_balance)."""
    user = await get_user(telegram_id)
    if not user:
        return False, 0.0

    balance = float(user["balance"])
    if balance < amount:
        return False, balance

    new_balance = balance - amount
    await update_balance(telegram_id, new_balance)
    await add_transaction(
        telegram_id, "game_entry", amount, "completed", description
    )
    return True, new_balance


async def credit_balance(
    telegram_id: int, amount: float, description: str
) -> float:
    """Credit winnings to user balance."""
    user = await get_user(telegram_id)
    if not user:
        return 0.0

    new_balance = float(user["balance"]) + amount
    await update_balance(telegram_id, new_balance)
    await add_transaction(telegram_id, "win", amount, "completed", description)
    return new_balance


async def get_deposit_by_fingerprint(fingerprint: str) -> dict | None:
    """Return an existing completed deposit carrying the same SMS fingerprint."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM transactions
            WHERE type = 'deposit' AND fingerprint = ? AND status = 'completed'
            LIMIT 1
            """,
            (fingerprint,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def auto_credit_deposit(
    telegram_id: int,
    amount: float,
    fingerprint: str,
    description: str,
) -> tuple[bool, float, bool]:
    """Credit a deposit that has already been verified.

    Returns (credited, new_balance, already_used). Works atomically so the
    receipt cannot be redeemed twice.
    """
    existing = await get_deposit_by_fingerprint(fingerprint)
    if existing:
        return False, await get_balance(telegram_id), True

    user = await get_user(telegram_id)
    if not user:
        return False, 0.0, False

    new_balance = float(user["balance"]) + amount

    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?",
            (new_balance, telegram_id),
        )
        await db.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, status, description, fingerprint)
            VALUES (?, 'deposit', ?, 'completed', ?, ?)
            """,
            (telegram_id, amount, description, fingerprint),
        )
        await db.commit()

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
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO telebirr_orders
                (merch_order_id, telegram_id, amount, status, checkout_url)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (merch_order_id, telegram_id, amount, checkout_url),
        )
        await db.commit()


async def get_telebirr_order(merch_order_id: str) -> dict | None:
    """Fetch a Telebirr order record by merchant order ID."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM telebirr_orders WHERE merch_order_id = ?",
            (merch_order_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def complete_telebirr_order(
    merch_order_id: str,
) -> tuple[bool, float, int]:
    """
    Idempotently mark an order as completed and credit the user's balance.

    Returns (credited, new_balance, telegram_id).
    credited=False if the order was already completed or not found.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row

        # Fetch order and lock it by checking status in a single transaction
        async with db.execute(
            "SELECT * FROM telebirr_orders WHERE merch_order_id = ?",
            (merch_order_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return False, 0.0, 0

        order = dict(row)
        if order["status"] != "pending":
            # Already processed — safe to return false (idempotent)
            balance = await get_balance(order["telegram_id"])
            return False, balance, order["telegram_id"]

        telegram_id: int = order["telegram_id"]
        amount: float = order["amount"]

        # Fetch current balance
        async with db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            user_row = await cursor.fetchone()

        if not user_row:
            return False, 0.0, telegram_id

        new_balance = float(user_row["balance"]) + amount

        # Mark order complete + credit balance + record transaction atomically
        await db.execute(
            "UPDATE telebirr_orders SET status = 'completed' WHERE merch_order_id = ?",
            (merch_order_id,),
        )
        await db.execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?",
            (new_balance, telegram_id),
        )
        await db.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, status, description)
            VALUES (?, 'deposit', ?, 'completed', ?)
            """,
            (telegram_id, amount, f"Telebirr deposit — order {merch_order_id}"),
        )
        await db.commit()

    return True, new_balance, telegram_id


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

    Returns True only for the first delivery of an event (subsequent
    at-least-once redeliveries are dropped).
    """
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO peerpay_events
                (event_id, delivery_id, event_type, object_id, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, delivery_id, event_type, object_id, payload),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_webhook_event(event_id: str) -> dict | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM peerpay_events WHERE event_id = ?", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def upsert_peerpay_deposit(
    payment_id: str,
    telegram_id: int,
    amount: float,
    currency: str,
    merchant_order_id: str | None,
    status: str,
) -> None:
    """Record/refresh a PeerPay deposit row from a status event.

    Never credits — status bookkeeping only. Existing credited state is kept.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO peerpay_deposits
                (payment_id, telegram_id, merchant_order_id, status, amount, currency)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                status = excluded.status,
                updated_at = datetime('now')
            """,
            (payment_id, telegram_id, merchant_order_id, status, amount, currency),
        )
        await db.commit()


async def get_peerpay_deposit(payment_id: str) -> dict | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM peerpay_deposits WHERE payment_id = ?", (payment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def credit_peerpay_deposit_once(
    payment_id: str,
    telegram_id: int,
    amount: float,
    merchant_order_id: str | None = None,
) -> tuple[bool, float, bool]:
    """Idempotently credit a confirmed PeerPay deposit.

    Returns (credited, new_balance, is_duplicate). Keyed by the PeerPay
    deposit resource id, so duplicate deliveries or events arriving out of
    order can never credit the wallet twice.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row

        # Ensure a base row exists even when deposit.created was missed.
        await db.execute(
            """
            INSERT OR IGNORE INTO peerpay_deposits
                (payment_id, telegram_id, merchant_order_id, status, amount)
            VALUES (?, ?, ?, 'succeeded', ?)
            """,
            (payment_id, telegram_id, merchant_order_id, amount),
        )

        async with db.execute(
            "SELECT telegram_id, credited FROM peerpay_deposits WHERE payment_id = ?",
            (payment_id,),
        ) as cursor:
            rec = await cursor.fetchone()

        if not rec:
            return False, 0.0, False

        target_id = int(rec["telegram_id"])
        async with db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?", (target_id,)
        ) as cursor:
            user_row = await cursor.fetchone()
        if not user_row:
            return False, 0.0, False

        current_balance = float(user_row["balance"])
        if rec["credited"]:
            return False, current_balance, True

        new_balance = current_balance + amount
        fingerprint = f"peerpay_dep:{payment_id}"

        await db.execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?",
            (new_balance, target_id),
        )
        await db.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, status, description, fingerprint)
            VALUES (?, 'deposit', ?, 'completed', ?, ?)
            """,
            (target_id, amount, f"PeerPay deposit — {payment_id}", fingerprint),
        )
        await db.execute(
            """
            UPDATE peerpay_deposits
            SET credited = 1, status = 'succeeded', updated_at = datetime('now')
            WHERE payment_id = ?
            """,
            (payment_id,),
        )
        await db.commit()

    return True, new_balance, False


async def create_peerpay_withdrawal_hold(
    payment_id: str,
    telegram_id: int,
    amount: float,
    status: str = "created",
    decision_code: str | None = None,
) -> None:
    """Record a PeerPay withdrawal hold after withdrawal creation."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO peerpay_withdrawals
                (payment_id, telegram_id, amount, status, decision_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payment_id, telegram_id, amount, status, decision_code),
        )
        await db.commit()


async def get_peerpay_withdrawal(payment_id: str) -> dict | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM peerpay_withdrawals WHERE payment_id = ?", (payment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_peerpay_withdrawal_progress(
    payment_id: str,
    status: str,
    verification_status: str | None = None,
    decision_code: str | None = None,
) -> None:
    """Persist non-wallet-mutating withdrawal events (the hold is unchanged)."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE peerpay_withdrawals
            SET status = ?,
                decision_code = CASE WHEN ? IS NULL THEN decision_code ELSE ? END,
                updated_at = datetime('now')
            WHERE payment_id = ?
            """,
            (status, decision_code, decision_code, payment_id),
        )
        await db.commit()


async def capture_peerpay_withdrawal_once(payment_id: str) -> tuple[bool, int]:
    """Capture the wallet hold for a completed withdrawal — exactly once.

    Returns (captured, telegram_id). The hold money was already deducted at
    creation, so capture only finalizes the ledger state.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT telegram_id, captured, released
            FROM peerpay_withdrawals WHERE payment_id = ?
            """,
            (payment_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False, 0
        if row["captured"] or row["released"]:
            return False, int(row["telegram_id"])

        await db.execute(
            """
            UPDATE peerpay_withdrawals
            SET captured = 1, hold_status = 'captured', status = 'succeeded',
                updated_at = datetime('now')
            WHERE payment_id = ? AND captured = 0 AND released = 0
            """,
            (payment_id,),
        )
        await db.commit()

    return True, int(row["telegram_id"])


async def release_peerpay_withdrawal_once(payment_id: str) -> tuple[bool, float]:
    """Release the hold for a failed/expired/cancelled withdrawal — exactly once.

    Returns (released, new_balance). Refunds the held amount back to the user
    wallet idempotently.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT telegram_id, amount, captured, released
            FROM peerpay_withdrawals WHERE payment_id = ?
            """,
            (payment_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row["captured"] or row["released"]:
            return False, 0.0

        target_id = int(row["telegram_id"])
        amount = float(row["amount"])

        async with db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?", (target_id,)
        ) as cursor:
            user_row = await cursor.fetchone()
        if not user_row:
            return False, 0.0

        new_balance = float(user_row["balance"]) + amount
        fingerprint = f"peerpay_wd_refund:{payment_id}"

        await db.execute(
            "UPDATE users SET balance = ? WHERE telegram_id = ?",
            (new_balance, target_id),
        )
        await db.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, status, description, fingerprint)
            VALUES (?, 'deposit', ?, 'completed', ?, ?)
            """,
            (target_id, amount, f"PeerPay withdrawal refund — {payment_id}", fingerprint),
        )
        await db.execute(
            """
            UPDATE peerpay_withdrawals
            SET released = 1, hold_status = 'released', status = 'released',
                updated_at = datetime('now')
            WHERE payment_id = ? AND captured = 0 AND released = 0
            """,
            (payment_id,),
        )
        await db.commit()

    return True, new_balance


async def record_house_revenue(
    room_id: str,
    cards_count: int,
    cut_per_card: float,
    total_revenue: float,
) -> None:
    """Record house commission earnings from a completed bingo round."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue)
            VALUES (?, ?, ?, ?)
            """,
            (room_id, cards_count, cut_per_card, total_revenue),
        )
        await db.commit()


async def get_house_revenue_summary() -> dict:
    """Return total income and per-room totals for house revenue."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT room_id, COUNT(*) as rounds, SUM(cards_count) as total_cards, SUM(total_revenue) as total_revenue FROM house_revenue GROUP BY room_id"
        ) as cursor:
            rows = await cursor.fetchall()
        by_room = {r["room_id"]: dict(r) for r in rows}
        async with db.execute("SELECT SUM(total_revenue) FROM house_revenue") as cursor:
            row = await cursor.fetchone()
            total = float(row[0] or 0.0) if row else 0.0
        return {"total_revenue": total, "by_room": by_room}

