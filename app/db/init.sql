-- ============================================================
-- Seat Reservation System
-- PostgreSQL schema initialization
-- ============================================================

-- ------------------------------------------------------------
-- ENUM TYPES
-- ------------------------------------------------------------

CREATE TYPE seat_status AS ENUM (
    'available',
    'held',
    'confirmed'
);

CREATE TYPE reservation_status AS ENUM (
    'confirmed',
    'cancelled',
    'expired'
);


-- ------------------------------------------------------------
-- SHOWS
-- ------------------------------------------------------------

CREATE TABLE shows (
    id UUID NOT NULL,
    name TEXT NOT NULL,
    price_paise BIGINT NOT NULL,
    per_user_limit INTEGER NOT NULL DEFAULT 4,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shows_pkey
        PRIMARY KEY (id),

    CONSTRAINT shows_price_paise_check
        CHECK (price_paise >= 0)
);


-- ------------------------------------------------------------
-- SEATS
-- ------------------------------------------------------------

CREATE TABLE seats (
    id UUID NOT NULL,
    show_id UUID NOT NULL,
    seat_number TEXT NOT NULL,
    status seat_status NOT NULL DEFAULT 'available',
    reservation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT seats_pkey
        PRIMARY KEY (id),

    CONSTRAINT seats_show_id_fkey
        FOREIGN KEY (show_id)
        REFERENCES shows(id)
        ON DELETE CASCADE,

    CONSTRAINT seats_show_id_seat_number_key
        UNIQUE (show_id, seat_number)
);


-- ------------------------------------------------------------
-- RESERVATIONS
-- ------------------------------------------------------------

CREATE TABLE reservations (
    id UUID NOT NULL,
    show_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    amount_paise BIGINT NOT NULL,
    status reservation_status NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT reservations_amount_paise_check
        CHECK (amount_paise >= 0),

    CONSTRAINT reservations_pkey
        PRIMARY KEY (id),

    CONSTRAINT reservations_show_id_fkey
        FOREIGN KEY (show_id)
        REFERENCES shows(id),

    CONSTRAINT reservations_show_id_user_id_idempotency_key_key
        UNIQUE (show_id, user_id, idempotency_key)
);


-- ------------------------------------------------------------
-- IDEMPOTENCY KEYS
-- ------------------------------------------------------------

CREATE TABLE idempotency_keys (
    id UUID NOT NULL,
    show_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    reservation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT idempotency_keys_pkey
        PRIMARY KEY (id),

    CONSTRAINT idempotency_keys_show_id_fkey
        FOREIGN KEY (show_id)
        REFERENCES shows(id),

    CONSTRAINT idempotency_keys_show_id_user_id_idempotency_key_key
        UNIQUE (show_id, user_id, idempotency_key)
);


-- ------------------------------------------------------------
-- RESERVATION SEATS
-- ------------------------------------------------------------

CREATE TABLE reservation_seats (
    reservation_id UUID NOT NULL,
    seat_id UUID NOT NULL,

    CONSTRAINT reservation_seats_pkey
        PRIMARY KEY (reservation_id, seat_id),

    CONSTRAINT reservation_seats_reservation_id_fkey
        FOREIGN KEY (reservation_id)
        REFERENCES reservations(id)
        ON DELETE CASCADE,

    CONSTRAINT reservation_seats_seat_id_fkey
        FOREIGN KEY (seat_id)
        REFERENCES seats(id),

    CONSTRAINT reservation_seats_seat_id_key
        UNIQUE (seat_id)
);


-- ------------------------------------------------------------
-- PER-USER SHOW LIMITS
-- ------------------------------------------------------------

CREATE TABLE user_show_limits (
    show_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    active_seat_count INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT user_show_limits_pkey
        PRIMARY KEY (show_id, user_id),

    CONSTRAINT user_show_limits_show_id_fkey
        FOREIGN KEY (show_id)
        REFERENCES shows(id)
);


-- ------------------------------------------------------------
-- REQUEST RATE LIMITS
-- ------------------------------------------------------------

CREATE TABLE request_rate_limits (
    user_id TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT request_rate_limits_pkey
        PRIMARY KEY (user_id, window_start),

    CONSTRAINT request_rate_limits_request_count_check
        CHECK (request_count >= 0)
);