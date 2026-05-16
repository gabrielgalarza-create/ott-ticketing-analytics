"""Sync SweatPals API data into local SQLite. Run with: python -m src.sync"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sweatpals_client import SweatPalsClient
from src.store import (
    connect, log_sync, upsert_members, upsert_orders, upsert_tickets, upsert_waitlist,
)


def sync_all(max_pages: int | None = None) -> None:
    client = SweatPalsClient()
    conn = connect()

    print("Probing auth header...")
    client._resolve_auth_header()
    print(f"  auth header: {client._auth_header_name}")

    plans = [
        ("orders",            client.orders,            lambda rows: upsert_orders(conn, rows)),
        ("new-tickets",       client.new_tickets,       lambda rows: upsert_tickets(conn, rows)),
        ("used-tickets",      client.used_tickets,      lambda rows: upsert_tickets(conn, rows)),
        ("claimed-tickets",   client.claimed_tickets,   lambda rows: upsert_tickets(conn, rows)),
        ("waitlist",          client.waitlist,          lambda rows: upsert_waitlist(conn, rows)),
        ("new-members",       client.new_members,       lambda rows: upsert_members(conn, rows, "active")),
        ("cancelled-members", client.cancelled_members, lambda rows: upsert_members(conn, rows, "cancelled")),
        ("renewed-members",   client.renewed_members,   lambda rows: upsert_members(conn, rows, "renewed")),
    ]

    import requests as _requests
    for name, fetcher, writer in plans:
        print(f"\n→ Syncing {name}...")
        try:
            rows = list(fetcher(max_pages=max_pages))
        except _requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                print(f"  [skip] {name}: endpoint not enabled on this account (404)")
                log_sync(conn, name, 0, 0)
                continue
            raise
        n = writer(rows)
        log_sync(conn, name, len(rows), n)
        print(f"  pulled {len(rows)} rows, upserted {n}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Cap pages per endpoint (useful for first-pull testing)")
    args = parser.parse_args()
    sync_all(max_pages=args.max_pages)
