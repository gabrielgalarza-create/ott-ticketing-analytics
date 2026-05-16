"""SQLite store for SweatPals data. Idempotent upserts so we can re-run the fetch safely."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sweatpals.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    sp_order_id TEXT,
    status TEXT,
    currency TEXT,
    created_at TEXT,
    paid_at TEXT,
    updated_at TEXT,
    refund_triggered_at TEXT,
    amount INTEGER,
    base_amount INTEGER,
    tax_amount INTEGER,
    discount_amount INTEGER,
    sp_fee INTEGER,
    stripe_fee INTEGER,
    refunded_amount INTEGER,
    sp_member_id TEXT,
    member_email TEXT,
    member_full_name TEXT,
    product_type TEXT,
    product_id TEXT,
    product_name TEXT,
    quantity INTEGER,
    event_id TEXT,
    event_name TEXT,
    event_start_at TEXT,
    membership_tier_name TEXT,
    booking_source TEXT,
    items_json TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_event_id ON orders(event_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_event_start ON orders(event_start_at);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    ticket_short_id TEXT,
    ticket_email TEXT,
    ticket_full_name TEXT,
    order_id TEXT,
    order_date TEXT,
    event_base_id TEXT,
    event_name TEXT,
    event_alias TEXT,
    event_address_name TEXT,
    event_instance_date TEXT,
    event_instance_end_date TEXT,
    event_price_amount INTEGER,
    event_price_tier_name TEXT,
    discount_code TEXT,
    discount_amount INTEGER,
    used_at TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_event_base ON tickets(event_base_id);
CREATE INDEX IF NOT EXISTS idx_tickets_order_date ON tickets(order_date);
CREATE INDEX IF NOT EXISTS idx_tickets_instance ON tickets(event_instance_date);

CREATE TABLE IF NOT EXISTS waitlist (
    id TEXT PRIMARY KEY,
    event_id TEXT,
    event_instance_date TEXT,
    user_id TEXT,
    user_email TEXT,
    user_full_name TEXT,
    is_spot_available INTEGER,
    is_early_access INTEGER,
    created_at TEXT,
    event_name TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_waitlist_event ON waitlist(event_id);

CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    state TEXT,
    membership_name TEXT,
    membership_tier_amount INTEGER,
    user_email TEXT,
    user_full_name TEXT,
    created_at TEXT,
    updated_at TEXT,
    renewed_at TEXT,
    renewal_type TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    endpoint TEXT PRIMARY KEY,
    last_synced_at TEXT,
    rows_seen INTEGER,
    rows_changed INTEGER
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_orders(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO orders (id, sp_order_id, status, currency, created_at, paid_at, updated_at,
                refund_triggered_at, amount, base_amount, tax_amount, discount_amount, sp_fee,
                stripe_fee, refunded_amount, sp_member_id, member_email, member_full_name,
                product_type, product_id, product_name, quantity, event_id, event_name,
                event_start_at, membership_tier_name, booking_source, items_json, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                paid_at=excluded.paid_at,
                updated_at=excluded.updated_at,
                refund_triggered_at=excluded.refund_triggered_at,
                refunded_amount=excluded.refunded_amount,
                raw_json=excluded.raw_json
            """,
            (
                r.get("id"), r.get("sp_order_id"), r.get("status"), r.get("currency"),
                r.get("created_at"), r.get("paid_at"), r.get("updated_at"),
                r.get("refund_triggered_at"), r.get("amount"), r.get("base_amount"),
                r.get("tax_amount"), r.get("discount_amount"), r.get("sp_fee"),
                r.get("stripe_fee"), r.get("refunded_amount"), r.get("sp_member_id"),
                r.get("member_email"), r.get("member_full_name"), r.get("product_type"),
                r.get("product_id"), r.get("product_name"), r.get("quantity"),
                r.get("event_id"), r.get("event_name"), r.get("event_start_at"),
                r.get("membership_tier_name"), r.get("booking_source"),
                json.dumps(r.get("items") or []), json.dumps(r),
            ),
        )
        n += 1
    conn.commit()
    return n


def upsert_tickets(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO tickets (id, ticket_short_id, ticket_email, ticket_full_name, order_id,
                order_date, event_base_id, event_name, event_alias, event_address_name,
                event_instance_date, event_instance_end_date, event_price_amount,
                event_price_tier_name, discount_code, discount_amount, used_at, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                used_at=excluded.used_at,
                raw_json=excluded.raw_json
            """,
            (
                r.get("id"), r.get("ticket_shortId"), r.get("ticket_email"), r.get("ticket_fullName"),
                r.get("order_id"), r.get("order_date"), r.get("event_baseId"),
                r.get("event_name"), r.get("event_alias"), r.get("event_addressName"),
                r.get("event_instanceDate"), r.get("event_instanceEndDate"),
                r.get("eventPrice_priceAmount"), r.get("eventPrice_tierName"),
                r.get("discount_code"), r.get("discount_amount"), r.get("ticket_usedAt"),
                json.dumps(r),
            ),
        )
        n += 1
    conn.commit()
    return n


def upsert_waitlist(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO waitlist (id, event_id, event_instance_date, user_id, user_email,
                user_full_name, is_spot_available, is_early_access, created_at, event_name, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                is_spot_available=excluded.is_spot_available,
                raw_json=excluded.raw_json
            """,
            (
                r.get("id"), r.get("eventId"), r.get("eventInstanceDate"), r.get("userId"),
                r.get("user_receiptsEmail"), r.get("user_fullName"),
                int(bool(r.get("waitlistedUsersToEvents_isSpotAvailable"))),
                int(bool(r.get("waitlistedUsersToEvents_isEarlyAccess"))),
                r.get("waitlistedUsersToEvents_createdAt"), r.get("event_name"),
                json.dumps(r),
            ),
        )
        n += 1
    conn.commit()
    return n


def upsert_members(conn: sqlite3.Connection, rows: Iterable[dict], state: str) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO members (id, state, membership_name, membership_tier_amount,
                user_email, user_full_name, created_at, updated_at, renewed_at, renewal_type, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,
                updated_at=excluded.updated_at,
                renewed_at=excluded.renewed_at,
                raw_json=excluded.raw_json
            """,
            (
                r.get("id"), state, r.get("membership_name"), r.get("membershipTier_amount"),
                r.get("user_email"), r.get("user_fullName"), r.get("createdAt"),
                r.get("updatedAt"), r.get("renewedAt"), r.get("renewalType"),
                json.dumps(r),
            ),
        )
        n += 1
    conn.commit()
    return n


def log_sync(conn: sqlite3.Connection, endpoint: str, rows_seen: int, rows_changed: int):
    from datetime import datetime, timezone
    conn.execute(
        """
        INSERT INTO sync_log (endpoint, last_synced_at, rows_seen, rows_changed)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            last_synced_at=excluded.last_synced_at,
            rows_seen=excluded.rows_seen,
            rows_changed=excluded.rows_changed
        """,
        (endpoint, datetime.now(timezone.utc).isoformat(), rows_seen, rows_changed),
    )
    conn.commit()
