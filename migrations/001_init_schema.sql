-- EtooBingo PostgreSQL Schema (migrated from SQLite)
--
-- Migration: 001_init_schema
-- Idempotent: safe to re-run (uses IF NOT EXISTS throughout)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    telegram_id       BIGINT        PRIMARY KEY,
    phone_number      TEXT          NOT NULL,
    username          TEXT,
    first_name        TEXT,
    balance           NUMERIC(14,2) NOT NULL DEFAULT 0.00
                                    CHECK (balance >= 0),
    total_deposited   NUMERIC(14,2) DEFAULT 0.00,
    total_withdrawn   NUMERIC(14,2) DEFAULT 0.00,
    total_won         NUMERIC(14,2) DEFAULT 0.00,
    games_played      INT           DEFAULT 0,
    registered_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_active_at    TIMESTAMPTZ   DEFAULT NOW(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 2. transactions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id                BIGSERIAL     PRIMARY KEY,
    telegram_id       BIGINT        NOT NULL
                                    REFERENCES users (telegram_id),
    type              TEXT          NOT NULL,
    amount            NUMERIC(14,2) NOT NULL,
    balance_after     NUMERIC(14,2),
    status            TEXT          NOT NULL DEFAULT 'completed',
    description       TEXT,
    fingerprint       TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 3. telebirr_orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telebirr_orders (
    merch_order_id    TEXT          PRIMARY KEY,
    telegram_id       BIGINT        NOT NULL
                                    REFERENCES users (telegram_id),
    amount            NUMERIC(14,2) NOT NULL,
    status            TEXT          NOT NULL DEFAULT 'pending',
    checkout_url      TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 4. peerpay_events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peerpay_events (
    event_id          TEXT          PRIMARY KEY,
    delivery_id       TEXT,
    event_type        TEXT          NOT NULL,
    object_id         TEXT,
    payload           TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 5. peerpay_deposits
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peerpay_deposits (
    payment_id        TEXT          PRIMARY KEY,
    telegram_id       BIGINT        NOT NULL
                                    REFERENCES users (telegram_id),
    merchant_order_id TEXT,
    status            TEXT          NOT NULL DEFAULT 'created',
    amount            NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    currency          TEXT          NOT NULL DEFAULT 'ETB',
    credited          BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 6. peerpay_withdrawals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peerpay_withdrawals (
    payment_id        TEXT          PRIMARY KEY,
    telegram_id       BIGINT        NOT NULL
                                    REFERENCES users (telegram_id),
    amount            NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    status            TEXT          NOT NULL DEFAULT 'created',
    hold_status       TEXT          NOT NULL DEFAULT 'held',
    captured          BOOLEAN       NOT NULL DEFAULT FALSE,
    released          BOOLEAN       NOT NULL DEFAULT FALSE,
    decision_code     TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 7. house_revenue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS house_revenue (
    id                BIGSERIAL     PRIMARY KEY,
    room_id           TEXT          NOT NULL,
    cards_count       INT           NOT NULL,
    cut_per_card      NUMERIC(14,2) NOT NULL,
    total_revenue     NUMERIC(14,2) NOT NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 8. active_rounds
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS active_rounds (
    room_id           TEXT          PRIMARY KEY,
    phase             TEXT          NOT NULL DEFAULT 'lobby',
    pot               NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    house_income      NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    called_numbers    JSONB         NOT NULL DEFAULT '[]'::jsonb,
    taken_cards       JSONB         NOT NULL DEFAULT '{}'::jsonb,
    player_data       JSONB         NOT NULL DEFAULT '{}'::jsonb,
    started_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 9. game_rounds  (new — historical record of completed rounds)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_rounds (
    round_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id           TEXT          NOT NULL,
    phase             TEXT          NOT NULL,
    entry_fee         NUMERIC(10,2) NOT NULL,
    house_cut_rate    NUMERIC(6,2)  NOT NULL,
    total_cards_sold  INT           NOT NULL DEFAULT 0,
    pot               NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    house_income      NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    called_numbers    JSONB         NOT NULL DEFAULT '[]'::jsonb,
    winners           JSONB         NOT NULL DEFAULT '[]'::jsonb,
    started_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    ended_at          TIMESTAMPTZ
);

-- ===========================================================================
-- INDEXES
-- ===========================================================================

-- users
CREATE INDEX IF NOT EXISTS idx_users_phone_number
    ON users (phone_number);

CREATE INDEX IF NOT EXISTS idx_users_last_active_at
    ON users (last_active_at);

-- transactions
CREATE INDEX IF NOT EXISTS idx_transactions_telegram_id
    ON transactions (telegram_id);

CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint
    ON transactions (fingerprint);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions (created_at);

CREATE INDEX IF NOT EXISTS idx_transactions_type_status
    ON transactions (type, status);

-- telebirr_orders
CREATE INDEX IF NOT EXISTS idx_telebirr_orders_telegram_id
    ON telebirr_orders (telegram_id);

CREATE INDEX IF NOT EXISTS idx_telebirr_orders_status
    ON telebirr_orders (status);

-- peerpay_events
CREATE INDEX IF NOT EXISTS idx_peerpay_events_object_id
    ON peerpay_events (object_id);

CREATE INDEX IF NOT EXISTS idx_peerpay_events_event_type
    ON peerpay_events (event_type);

-- peerpay_deposits
CREATE INDEX IF NOT EXISTS idx_peerpay_deposits_telegram_id
    ON peerpay_deposits (telegram_id);

CREATE INDEX IF NOT EXISTS idx_peerpay_deposits_status
    ON peerpay_deposits (status);

CREATE INDEX IF NOT EXISTS idx_peerpay_deposits_merchant_order_id
    ON peerpay_deposits (merchant_order_id);

-- peerpay_withdrawals
CREATE INDEX IF NOT EXISTS idx_peerpay_withdrawals_telegram_id
    ON peerpay_withdrawals (telegram_id);

CREATE INDEX IF NOT EXISTS idx_peerpay_withdrawals_status
    ON peerpay_withdrawals (status);

-- house_revenue
CREATE INDEX IF NOT EXISTS idx_house_revenue_room_id
    ON house_revenue (room_id);

CREATE INDEX IF NOT EXISTS idx_house_revenue_created_at
    ON house_revenue (created_at);

-- game_rounds
CREATE INDEX IF NOT EXISTS idx_game_rounds_room_id
    ON game_rounds (room_id);

CREATE INDEX IF NOT EXISTS idx_game_rounds_phase
    ON game_rounds (phase);

CREATE INDEX IF NOT EXISTS idx_game_rounds_started_at
    ON game_rounds (started_at);

CREATE INDEX IF NOT EXISTS idx_game_rounds_ended_at
    ON game_rounds (ended_at);
