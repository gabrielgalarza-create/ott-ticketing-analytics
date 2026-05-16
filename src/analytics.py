"""Pure analytics functions over the tickets table. Test-friendly: no Streamlit here.

Note: `/orders` endpoint isn't enabled on this account, so all metrics are derived from the
`tickets` table populated by `/new-tickets`. Each row = one attendee ticket. We treat the
unique event instance as `(event_base_id, event_instance_date)` so that recurring events
(weekly/monthly Blends, etc.) are tracked as separate instances.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.store import connect


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CAPACITY_CSV = CONFIG_DIR / "capacities.csv"


def _to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def load_tickets() -> pd.DataFrame:
    conn = connect()
    df = pd.read_sql_query("SELECT * FROM tickets", conn)
    conn.close()
    if df.empty:
        return df
    df["order_date"] = _to_utc(df["order_date"])
    df["event_instance_date"] = _to_utc(df["event_instance_date"])
    df["event_instance_end_date"] = _to_utc(df["event_instance_end_date"])
    df["used_at"] = _to_utc(df["used_at"])
    df["event_price_amount"] = pd.to_numeric(df["event_price_amount"], errors="coerce").fillna(0)
    df["discount_amount"] = pd.to_numeric(df["discount_amount"], errors="coerce").fillna(0)
    df["net_price"] = (df["event_price_amount"] - df["discount_amount"]).clip(lower=0)
    df["is_free"] = df["event_price_amount"] == 0
    # Stable instance key for one specific occurrence of a recurring event
    df["instance_key"] = df["event_base_id"].astype(str) + "::" + df["event_instance_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").fillna("unknown")
    df["was_used"] = df["used_at"].notna()
    return df


# Industry benchmarks (used as a fallback when OTT doesn't have enough data for a series)
DEFAULT_PAID_SHOW_UP_RATE = 0.75   # 70–80%, midpoint 75%
DEFAULT_FREE_SHOW_UP_RATE = 0.45   # 40–50%, midpoint 45%
MIN_SAMPLE_FOR_SERIES_RATE = 100   # need ≥100 tickets across past instances of a series before we trust its own rate


def show_up_rates(tickets: pd.DataFrame) -> dict[str, float]:
    """Compute OTT's overall paid/free show-up rates from past events. Falls back to industry
    benchmarks if there's no historical data."""
    if tickets.empty:
        return {"paid": DEFAULT_PAID_SHOW_UP_RATE, "free": DEFAULT_FREE_SHOW_UP_RATE,
                "paid_n": 0, "free_n": 0, "paid_used_for_default": True, "free_used_for_default": True}
    now = pd.Timestamp.now(tz="UTC")
    past = tickets[tickets["event_instance_date"] < now]
    rates = {}
    for label, mask in [("paid", ~past["is_free"]), ("free", past["is_free"])]:
        sub = past[mask]
        n = len(sub)
        if n == 0:
            rates[label] = (DEFAULT_PAID_SHOW_UP_RATE if label == "paid" else DEFAULT_FREE_SHOW_UP_RATE)
            rates[f"{label}_n"] = 0
            rates[f"{label}_used_for_default"] = True
        else:
            rates[label] = sub["was_used"].sum() / n
            rates[f"{label}_n"] = n
            rates[f"{label}_used_for_default"] = False
    return rates


def series_show_up_rate(tickets: pd.DataFrame, event_base_id: str, event_name: str | None = None) -> dict[str, float | None]:
    """Show-up rate for a specific event series, if we have enough past data. Returns
    {"paid": rate or None, "free": rate or None, "n_paid": int, "n_free": int}."""
    if tickets.empty:
        return {"paid": None, "free": None, "n_paid": 0, "n_free": 0}
    now = pd.Timestamp.now(tz="UTC")
    past = tickets[
        (tickets["event_instance_date"] < now) &
        ((tickets["event_base_id"] == event_base_id) | (tickets["event_name"] == event_name))
    ]
    out = {}
    for label, mask in [("paid", ~past["is_free"]), ("free", past["is_free"])]:
        sub = past[mask]
        n = len(sub)
        out[f"n_{label}"] = n
        out[label] = (sub["was_used"].sum() / n) if n >= MIN_SAMPLE_FOR_SERIES_RATE else None
    return out


def expected_attendance(paid_sold: int, free_sold: int, tickets_for_rates: pd.DataFrame,
                        event_base_id: str | None = None, event_name: str | None = None) -> dict:
    """Project attendance for an upcoming event. Uses series-specific rate if we have ≥100 past
    tickets in that series; otherwise falls back to OTT's overall paid/free rates."""
    overall = show_up_rates(tickets_for_rates)
    series = series_show_up_rate(tickets_for_rates, event_base_id, event_name) if event_base_id else {"paid": None, "free": None}
    paid_rate = series["paid"] if series["paid"] is not None else overall["paid"]
    free_rate = series["free"] if series["free"] is not None else overall["free"]
    paid_source = "series" if series["paid"] is not None else ("portfolio" if not overall["paid_used_for_default"] else "industry-default")
    free_source = "series" if series["free"] is not None else ("portfolio" if not overall["free_used_for_default"] else "industry-default")
    return {
        "expected": int(round(paid_sold * paid_rate + free_sold * free_rate)),
        "paid_rate": paid_rate,
        "free_rate": free_rate,
        "paid_source": paid_source,
        "free_source": free_source,
    }


def load_capacities() -> pd.DataFrame:
    if not CAPACITY_CSV.exists():
        return pd.DataFrame(columns=["instance_key", "event_name", "event_instance_date", "capacity"])
    return pd.read_csv(CAPACITY_CSV)


def load_waitlist() -> pd.DataFrame:
    conn = connect()
    df = pd.read_sql_query("SELECT * FROM waitlist", conn)
    conn.close()
    if df.empty:
        return df
    df["event_instance_date"] = _to_utc(df["event_instance_date"])
    df["instance_key"] = df["event_id"].astype(str) + "::" + df["event_instance_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").fillna("unknown")
    return df


def event_summary(tickets: pd.DataFrame, capacities: pd.DataFrame, waitlist: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per event instance (event_base_id, event_instance_date)."""
    if tickets.empty:
        return pd.DataFrame()
    now = now or pd.Timestamp.now(tz="UTC")

    grouped = tickets.groupby(
        ["instance_key", "event_base_id", "event_name", "event_alias", "event_address_name", "event_instance_date"],
        dropna=False,
    ).agg(
        tickets_sold=("id", "count"),
        paid_tickets=("is_free", lambda s: (~s).sum()),
        free_tickets=("is_free", "sum"),
        gross_revenue=("event_price_amount", "sum"),
        discounts=("discount_amount", "sum"),
        net_revenue=("net_price", "sum"),
        unique_orders=("order_id", "nunique"),
        first_sale=("order_date", "min"),
        last_sale=("order_date", "max"),
        attended_count=("was_used", "sum"),
        paid_attended=("was_used", lambda s: (s & ~tickets.loc[s.index, "is_free"]).sum()),
        free_attended=("was_used", lambda s: (s & tickets.loc[s.index, "is_free"]).sum()),
    ).reset_index()

    grouped["days_until_event"] = (grouped["event_instance_date"] - now).dt.days
    grouped["days_on_sale"] = (now - grouped["first_sale"]).dt.days.clip(lower=0)
    grouped["status"] = grouped["days_until_event"].apply(
        lambda d: "past" if pd.isna(d) or d < 0 else ("imminent" if d <= 7 else "upcoming")
    )

    if not capacities.empty and "instance_key" in capacities.columns:
        cap = capacities[["instance_key", "capacity"]].copy()
        cap["capacity"] = pd.to_numeric(cap["capacity"], errors="coerce")
        grouped = grouped.merge(cap, on="instance_key", how="left")
    else:
        grouped["capacity"] = pd.NA
    grouped["sell_through_pct"] = (grouped["tickets_sold"] / grouped["capacity"]).where(grouped["capacity"].notna()) * 100

    if not waitlist.empty:
        wl = waitlist.groupby("instance_key").size().rename("waitlist_count").reset_index()
        grouped = grouped.merge(wl, on="instance_key", how="left")
    else:
        grouped["waitlist_count"] = 0
    grouped["waitlist_count"] = grouped["waitlist_count"].fillna(0).astype(int)

    grouped["attendance_rate_pct"] = (grouped["attended_count"] / grouped["tickets_sold"]) * 100
    grouped["paid_show_up_pct"] = (grouped["paid_attended"] / grouped["paid_tickets"]).where(grouped["paid_tickets"] > 0) * 100
    grouped["free_show_up_pct"] = (grouped["free_attended"] / grouped["free_tickets"]).where(grouped["free_tickets"] > 0) * 100

    # Expected attendance — only meaningful for upcoming events
    expected_list, paid_rate_list, free_rate_list, source_list = [], [], [], []
    for _, r in grouped.iterrows():
        if r["status"] == "past":
            expected_list.append(int(r["attended_count"]))
            paid_rate_list.append(r["paid_show_up_pct"] / 100 if pd.notna(r["paid_show_up_pct"]) else None)
            free_rate_list.append(r["free_show_up_pct"] / 100 if pd.notna(r["free_show_up_pct"]) else None)
            source_list.append("actual")
        else:
            est = expected_attendance(
                int(r["paid_tickets"]), int(r["free_tickets"]),
                tickets, event_base_id=r["event_base_id"], event_name=r["event_name"],
            )
            expected_list.append(est["expected"])
            paid_rate_list.append(est["paid_rate"])
            free_rate_list.append(est["free_rate"])
            source_list.append(f"paid:{est['paid_source']}/free:{est['free_source']}")
    grouped["expected_attendance"] = expected_list
    grouped["paid_rate_used"] = paid_rate_list
    grouped["free_rate_used"] = free_rate_list
    grouped["rate_source"] = source_list

    return grouped.sort_values("event_instance_date")


def sales_curve(tickets: pd.DataFrame, instance_key: str) -> pd.DataFrame:
    """Cumulative tickets and revenue by `days_before_event` for one event instance."""
    e = tickets[tickets["instance_key"] == instance_key].copy()
    if e.empty:
        return pd.DataFrame(columns=["order_date", "days_before_event", "tickets_cum", "revenue_cum"])
    e = e.sort_values("order_date")
    e["days_before_event"] = (e["event_instance_date"] - e["order_date"]).dt.days
    e["tickets_cum"] = range(1, len(e) + 1)
    e["revenue_cum"] = e["net_price"].cumsum()
    return e[["order_date", "days_before_event", "tickets_cum", "revenue_cum"]]


def comparable_curve(tickets: pd.DataFrame, event_row: pd.Series, n_recent: int = 5) -> pd.DataFrame:
    """Avg sales curve from the most recent N PAST instances of the same event_base_id (if available)
    or same event_name otherwise. Returns days_before_event -> mean tickets_pct_of_final."""
    if tickets.empty:
        return pd.DataFrame()

    target_key = event_row["instance_key"]
    base_id = event_row.get("event_base_id")
    name = event_row.get("event_name")
    now = pd.Timestamp.now(tz="UTC")

    # Match by event_base_id first (most reliable for recurring series), fall back to name
    past = tickets[
        (tickets["instance_key"] != target_key) &
        (tickets["event_instance_date"] < now) &
        ((tickets["event_base_id"] == base_id) | (tickets["event_name"] == name))
    ]
    if past.empty:
        return pd.DataFrame()

    # Pick the N most recent past instances
    recent_keys = (
        past[["instance_key", "event_instance_date"]]
        .drop_duplicates()
        .sort_values("event_instance_date", ascending=False)
        .head(n_recent)["instance_key"]
        .tolist()
    )

    curves = []
    for k in recent_keys:
        c = sales_curve(tickets, k)
        if c.empty:
            continue
        total = c["tickets_cum"].iloc[-1]
        if total <= 0:
            continue
        c["tickets_pct_of_final"] = c["tickets_cum"] / total * 100
        c["instance_key"] = k
        curves.append(c)
    if not curves:
        return pd.DataFrame()
    combined = pd.concat(curves, ignore_index=True)
    avg = combined.groupby("days_before_event")["tickets_pct_of_final"].mean().reset_index()
    return avg.sort_values("days_before_event", ascending=False)


def pace_flag(event_row: pd.Series, comparable: pd.DataFrame) -> tuple[str, str]:
    days_out = event_row.get("days_until_event")
    if pd.isna(days_out) or days_out < 0:
        return ("past", "event already happened")
    if comparable.empty:
        return ("no_baseline", "no comparable past instances yet — needs ≥1 past run of this event")

    expected_pct = comparable.loc[(comparable["days_before_event"] - days_out).abs().idxmin(), "tickets_pct_of_final"]
    sold = event_row["tickets_sold"]
    cap = event_row.get("capacity")

    if pd.isna(cap):
        return ("no_baseline", "no capacity set; add it in capacities.csv to enable pace flag")

    actual_pct = (sold / cap) * 100
    delta = actual_pct - expected_pct
    if delta < -10:
        return ("behind", f"{actual_pct:.0f}% sold; comparable past events were ~{expected_pct:.0f}% with {int(days_out)}d to go")
    if delta > 10:
        return ("ahead", f"{actual_pct:.0f}% sold vs ~{expected_pct:.0f}% historical pace")
    return ("on_pace", f"{actual_pct:.0f}% sold vs ~{expected_pct:.0f}% historical")


def daily_velocity(tickets: pd.DataFrame, days: int = 30) -> pd.DataFrame:
    if tickets.empty:
        return pd.DataFrame()
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    recent = tickets[tickets["order_date"] >= cutoff].copy()
    if recent.empty:
        return pd.DataFrame()
    recent["day"] = recent["order_date"].dt.tz_convert("UTC").dt.date
    daily = recent.groupby("day").agg(
        tickets=("id", "count"),
        revenue=("net_price", "sum"),
        orders=("order_id", "nunique"),
    ).reset_index()
    return daily


def write_capacity_template(tickets: pd.DataFrame, path: Path = CAPACITY_CSV) -> int:
    """Generate a CSV stub of all known event instances. Preserves any capacities you've already set."""
    if tickets.empty:
        return 0
    instances = tickets[["instance_key", "event_name", "event_address_name", "event_instance_date"]].drop_duplicates(subset=["instance_key"])
    instances = instances.sort_values("event_instance_date")
    instances["capacity"] = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_csv(path)
        if "instance_key" in existing.columns and "capacity" in existing.columns:
            existing_subset = existing[["instance_key", "capacity"]].rename(columns={"capacity": "existing_capacity"})
            merged = instances.merge(existing_subset, on="instance_key", how="left")
            merged["capacity"] = merged["existing_capacity"].combine_first(merged["capacity"].astype(object))
            merged = merged.drop(columns=["existing_capacity"])
            merged.to_csv(path, index=False)
            return len(merged)
    instances.to_csv(path, index=False)
    return len(instances)
