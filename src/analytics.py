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
    # instance_key = event_base_id. Each recurring event gets its own base_id in SweatPals;
    # reschedules keep the same base_id with a different instance_date — we treat them as
    # the SAME event, using the max(date) as the canonical date below.
    df["instance_key"] = df["event_base_id"].astype(str)
    # Override per-ticket instance_date with the canonical (latest) date for that base_id
    canonical = df.groupby("event_base_id")["event_instance_date"].transform("max")
    df["event_instance_date"] = canonical
    df["was_used"] = df["used_at"].notna()
    return df


# Industry benchmarks (used as a fallback when OTT doesn't have enough data for a series)
DEFAULT_PAID_SHOW_UP_RATE = 0.75   # 70–80%, midpoint 75%
DEFAULT_FREE_SHOW_UP_RATE = 0.45   # 40–50%, midpoint 45%
MIN_SAMPLE_FOR_SERIES_RATE = 100   # need ≥100 tickets across past instances of a series before we trust its own rate
SURGE_WINDOW_DAYS = 7              # "week-of surge" window — final-N-days share of ticket sales


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
    df["instance_key"] = df["event_id"].astype(str)
    return df


def event_index(tickets: pd.DataFrame, waitlist: pd.DataFrame | None = None) -> pd.DataFrame:
    """Canonical event universe: every event we know about, from tickets PLUS waitlist-only
    events (on pre-sale/waitlist before ticket sales open, so they have 0 tickets yet).

    Returns columns: instance_key, event_name, event_address_name, event_instance_date,
    event_base_id, has_tickets.
    """
    frames = []
    if tickets is not None and not tickets.empty:
        tev = tickets.groupby("instance_key", as_index=False).agg(
            event_name=("event_name", "last"),
            event_address_name=("event_address_name", "last"),
            event_instance_date=("event_instance_date", "last"),
            event_base_id=("event_base_id", "last"),
        )
        tev["has_tickets"] = True
        frames.append(tev)
    known = set(frames[0]["instance_key"].astype(str)) if frames else set()
    if waitlist is not None and not waitlist.empty and "instance_key" in waitlist.columns:
        wev = waitlist.groupby("instance_key", as_index=False).agg(
            event_name=("event_name", "last"),
            event_instance_date=("event_instance_date", "last"),
        )
        wev["instance_key"] = wev["instance_key"].astype(str)
        wev = wev[~wev["instance_key"].isin(known)]
        if not wev.empty:
            wev["event_address_name"] = ""
            wev["event_base_id"] = wev["instance_key"]
            wev["has_tickets"] = False
            frames.append(wev)
    if not frames:
        return pd.DataFrame(columns=["instance_key", "event_name", "event_address_name",
                                     "event_instance_date", "event_base_id", "has_tickets"])
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values("event_instance_date")


def event_summary(tickets: pd.DataFrame, capacities: pd.DataFrame, waitlist: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per event instance (event_base_id, event_instance_date)."""
    if tickets.empty:
        return pd.DataFrame()
    now = now or pd.Timestamp.now(tz="UTC")

    # Group by instance_key only; take the latest value for metadata fields so reschedules
    # (where address/name/alias may have changed) collapse correctly into one row.
    grouped = tickets.sort_values("order_date").groupby("instance_key", dropna=False).agg(
        event_base_id=("event_base_id", "last"),
        event_name=("event_name", "last"),
        event_alias=("event_alias", "last"),
        event_address_name=("event_address_name", "last"),
        event_instance_date=("event_instance_date", "last"),
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

    # Add waitlist-only events (on pre-sale/waitlist before tickets open → 0 tickets yet)
    if waitlist is not None and not waitlist.empty and "instance_key" in waitlist.columns:
        known = set(grouped["instance_key"].astype(str))
        wl_ev = waitlist.groupby("instance_key", as_index=False).agg(
            event_name=("event_name", "last"),
            event_instance_date=("event_instance_date", "last"),
        )
        wl_ev["instance_key"] = wl_ev["instance_key"].astype(str)
        wl_ev = wl_ev[~wl_ev["instance_key"].isin(known)]
        if not wl_ev.empty:
            wl_ev["event_base_id"] = wl_ev["instance_key"]
            wl_ev["event_alias"] = ""
            wl_ev["event_address_name"] = ""
            for col in ("tickets_sold", "paid_tickets", "free_tickets", "gross_revenue",
                        "discounts", "net_revenue", "unique_orders", "attended_count",
                        "paid_attended", "free_attended"):
                wl_ev[col] = 0
            wl_ev["first_sale"] = pd.NaT
            wl_ev["last_sale"] = pd.NaT
            grouped = pd.concat([grouped, wl_ev], ignore_index=True)
            # Concat can coerce tz-aware datetime columns to object — restore datetime dtype
            for col in ("event_instance_date", "first_sale", "last_sale"):
                grouped[col] = pd.to_datetime(grouped[col], utc=True, errors="coerce")

    grouped["days_until_event"] = (grouped["event_instance_date"] - now).dt.days
    grouped["days_on_sale"] = (now - grouped["first_sale"]).dt.days.clip(lower=0)
    grouped["status"] = grouped["days_until_event"].apply(
        lambda d: "past" if pd.isna(d) or d < 0 else ("imminent" if d <= 7 else "upcoming")
    )

    if not capacities.empty and "instance_key" in capacities.columns:
        cap_cols = ["instance_key", "capacity"]
        if "target_tickets" in capacities.columns:
            cap_cols.append("target_tickets")
        cap = capacities[cap_cols].copy()
        cap["capacity"] = pd.to_numeric(cap["capacity"], errors="coerce")
        if "target_tickets" in cap.columns:
            cap["target_tickets"] = pd.to_numeric(cap["target_tickets"], errors="coerce")
        grouped = grouped.merge(cap, on="instance_key", how="left")
    else:
        grouped["capacity"] = pd.NA
        grouped["target_tickets"] = pd.NA
    if "target_tickets" not in grouped.columns:
        grouped["target_tickets"] = pd.NA
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

    # Forecast final ticket sales (only for upcoming events)
    forecasts, forecast_lows, forecast_highs, forecast_methods = [], [], [], []
    surge_shares, surge_tix = [], []
    for _, r in grouped.iterrows():
        if r["status"] == "past":
            forecasts.append(int(r["tickets_sold"]))
            forecast_lows.append(int(r["tickets_sold"]))
            forecast_highs.append(int(r["tickets_sold"]))
            forecast_methods.append("actual")
            surge_shares.append(None); surge_tix.append(None)
        else:
            fc = forecast_final_tickets(tickets, r)
            forecasts.append(fc.get("forecast"))
            forecast_lows.append(fc.get("low"))
            forecast_highs.append(fc.get("high"))
            if fc.get("forecast") is not None:
                forecast_methods.append(fc.get("method", f"median of n={fc['n_comparables']} comparables"))
            else:
                forecast_methods.append(fc.get("reason", "n/a"))
            surge_shares.append(fc.get("surge_share"))
            surge_tix.append(fc.get("surge_tickets"))
    grouped["forecast_final"] = forecasts
    grouped["forecast_low"] = forecast_lows
    grouped["forecast_high"] = forecast_highs
    grouped["forecast_method"] = forecast_methods
    grouped["surge_share"] = surge_shares
    grouped["surge_tickets"] = surge_tix

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

    # Match by event_base_id, exact name, OR same-series token overlap (e.g., both contain "Fit Fest")
    target_tokens = _series_tokens(name or "")
    series_match = tickets["event_name"].apply(lambda n: bool(target_tokens & _series_tokens(n or "")))
    past = tickets[
        (tickets["instance_key"] != target_key) &
        (tickets["event_instance_date"] < now) &
        ((tickets["event_base_id"] == base_id) | (tickets["event_name"] == name) | series_match)
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


def _series_tokens(name: str) -> set[str]:
    """Loose tokens for matching same-series events ('Fit Fest', 'Blend', 'Yacht', ...)."""
    if not name:
        return set()
    stop = {"the", "and", "of", "a", "in", "for", "on", "to", "with", "edition", "weekend",
            "party", "series", "day", "night", "@", "&"}
    tokens = {t.strip(":-,.").lower() for t in name.split() if len(t) > 2}
    return {t for t in tokens if t not in stop}


def forecast_final_tickets(tickets: pd.DataFrame, event_row: pd.Series,
                           price_band_pct: float = 0.6, min_final: int = 150,
                           n_recent: int = 5) -> dict:
    """Forecast the final ticket count for an upcoming event using comparable past events.

    Method: find past comparable paid events (same series by name overlap, OR within
    ±price_band_pct of this event's mode price, with ≥min_final tickets). For each, compute
    how many tickets they sold from `days_out` onward (= final − cum_at_stage). Forecast =
    current_sold + median(tickets_remaining). This works even when comparables hadn't started
    selling yet at this days-out — they simply contribute their full final as "remaining."

    Crucially it also forecasts events that haven't started selling (waitlist/pre-sale, 0 sold):
    series like The Blend launch slow and back-load most sales into the final weeks, so a 0-sold
    Blend 23 days out still projects to ~its comparable Blends' finals via `tickets_remaining`.

    Returns {"forecast", "low", "high", "comparables": [...], "method"} or {"forecast": None, "reason"}.
    """
    if tickets.empty:
        return {"forecast": None, "reason": "no historical data"}
    days_out = event_row.get("days_until_event")
    if pd.isna(days_out) or days_out < 0:
        return {"forecast": None, "reason": "event is past"}
    current_sold = int(event_row["tickets_sold"])

    now = pd.Timestamp.now(tz="UTC")
    target_key = event_row["instance_key"]
    target_name = event_row.get("event_name", "")
    target_tokens = _series_tokens(target_name)

    # Mode price for this event — may be unknown for a 0-sold waitlist/pre-sale event. That's fine:
    # we can still match comparables by series. Only when there's NEITHER a price NOR a recognizable
    # series do we have no basis to forecast.
    this_event = tickets[tickets["instance_key"] == target_key]
    paid_prices = (this_event[~this_event["is_free"]]["event_price_amount"]
                   if not this_event.empty else pd.Series(dtype=float))
    target_price = float(paid_prices.mode().iloc[0]) if not paid_prices.empty else None
    if target_price is None and not target_tokens:
        return {"forecast": None, "reason": "no price or series to match comparables"}

    # Build comparables: past paid events, ≥min_final tickets, EITHER same-series OR price-band
    past = tickets[(tickets["event_instance_date"] < now) & (~tickets["is_free"])]
    if past.empty:
        return {"forecast": None, "reason": "no past paid events"}
    meta = past.groupby("instance_key").agg(
        name=("event_name", "first"),
        date=("event_instance_date", "first"),
        sold=("id", "count"),
        mode_price=("event_price_amount", lambda s: s.mode().iloc[0]),
    ).reset_index()
    same_series = meta["name"].apply(lambda n: bool(target_tokens & _series_tokens(n)))
    if target_price is not None:
        lo, hi = target_price * (1 - price_band_pct), target_price * (1 + price_band_pct)
        in_price_band = (meta["mode_price"] >= lo) & (meta["mode_price"] <= hi)
    else:
        lo = hi = None
        in_price_band = pd.Series(False, index=meta.index)
    eligible = meta[(in_price_band | same_series) & (meta["sold"] >= min_final)].copy()
    if eligible.empty:
        band_txt = f"price band ${lo:.0f}-${hi:.0f}" if lo is not None else f"the '{target_name}' series"
        return {"forecast": None, "reason": f"no past events in {band_txt} with ≥{min_final} tickets"}
    eligible["same_series"] = eligible["name"].apply(lambda n: bool(target_tokens & _series_tokens(n)))

    # Not-yet-on-sale events (waitlist/pre-sale, 0 sold) have no trajectory to extend, and
    # "tickets remaining from this stage" is distorted by how front- vs back-loaded each past
    # event was. Series like The Blend also scale up over time, so a stale first-run instance
    # shouldn't anchor the forecast. For these we project from the TYPICAL FINAL of the most
    # recent comparables (same window the pace flag uses) instead.
    not_started = current_sold == 0
    if not_started:
        eligible = eligible.sort_values("date", ascending=False).head(n_recent)

    # How long THIS event has been on sale. We align comparables by *days on sale* (elapsed
    # selling time), NOT by days-before-event: past runs launched anywhere from ~17 to ~54 days
    # out, so "tickets remaining after this many days BEFORE the event" unfairly credits a late
    # launch with the early-bird sales an early launch had already banked. Aligning on days-on-
    # sale asks the right question — "from the same point in its selling life, how much more did
    # a comparable sell?" — which stops early launchers from dragging the forecast down.
    target_curve = sales_curve(tickets, target_key)
    target_dos = max(0, int(target_curve["days_before_event"].max() - days_out)) if not target_curve.empty else 0

    # For each comparable: tickets sold from the equivalent days-on-sale point onward
    # (= final - cum_at_stage), plus the share of final sold in the last SURGE_WINDOW_DAYS.
    remaining_vals = []
    final_vals = []
    surge_shares = []
    used = []
    for _, c in eligible.iterrows():
        curve = sales_curve(tickets, c["instance_key"])
        if curve.empty:
            continue
        final = int(curve["tickets_cum"].iloc[-1])
        if final <= 0:
            continue
        # Equivalent stage. Two analogs of "where our event is now": event-proximity (days_out)
        # and elapsed selling time (comp_launch - target_dos). We start counting the comparable's
        # remaining from whichever is FURTHER from the event (larger days-before-event), i.e. the
        # fuller run. days_out alone undercounts a LATE launch (early launchers had already banked
        # their early-bird sales); days-on-sale alone undercounts an EARLY launch whose late-
        # launching comparables had already finished selling. The max() reconciles both.
        comp_launch = int(curve["days_before_event"].max())
        ref_dbe = max(days_out, comp_launch - target_dos)
        at_stage = curve[curve["days_before_event"] >= ref_dbe]
        cum_at_stage = int(at_stage["tickets_cum"].max()) if not at_stage.empty else 0
        remaining = final - cum_at_stage
        remaining_vals.append(remaining)
        final_vals.append(final)
        # Final-week surge: tickets sold in the last SURGE_WINDOW_DAYS / final
        at_surge = curve[curve["days_before_event"] >= SURGE_WINDOW_DAYS]
        cum_at_surge = int(at_surge["tickets_cum"].max()) if not at_surge.empty else 0
        surge_shares.append((final - cum_at_surge) / final)
        used.append({
            "name": c["name"], "date": c["date"], "final": final,
            "mode_price": float(c["mode_price"]),
            "cum_at_stage": cum_at_stage,
            "pct_at_stage": round(cum_at_stage / final * 100, 1),
            "remaining_from_stage": remaining,
            "same_series": bool(c["same_series"]),
        })

    if not remaining_vals:
        return {"forecast": None, "reason": "no usable comparables"}

    import statistics
    def _pct(sorted_vals, frac):
        m = len(sorted_vals)
        idx = min(m - 1, max(0, int(frac * m)))
        return sorted_vals[idx]

    if not_started:
        # Project from the typical FINAL of recent comparables (no current trajectory to extend).
        finals_sorted = sorted(final_vals)
        median_final = statistics.median(finals_sorted)
        forecast = int(round(median_final))
        low = int(_pct(finals_sorted, 0.25))
        high = int(_pct(finals_sorted, 0.75))
        median_remaining = int(median_final)  # = forecast since current_sold is 0
        basis = "recent-finals"
        method = (f"median final of n={len(final_vals)} recent comparables "
                  f"(event not yet on sale — projecting to series' typical finish)")
    else:
        # Extend the current trajectory: current sold + how much comparables sold from here on.
        remaining_sorted = sorted(remaining_vals)
        median_remaining = statistics.median(remaining_sorted)
        forecast = int(current_sold + median_remaining)
        low = int(current_sold + _pct(remaining_sorted, 0.25))
        high = int(current_sold + _pct(remaining_sorted, 0.75))
        basis = "remaining-from-equivalent-days-on-sale"
        band_lbl = (f"in ${lo:.0f}-${hi:.0f} band" if lo is not None else "same-series")
        method = (f"current sold + median remaining from n={len(remaining_vals)} comparables "
                  f"at equal days-on-sale ({target_dos}d) {band_lbl}")

    # Week-of surge: historically what % of an event's final sales land in the last week.
    surge_share = statistics.median(surge_shares) if surge_shares else 0.0
    # If this event is still BEFORE its final week, that surge is still ahead of it.
    surge_ahead = days_out > SURGE_WINDOW_DAYS
    surge_tickets = int(round(forecast * surge_share)) if surge_ahead else 0

    return {
        "forecast": forecast,
        "low": low,
        "high": high,
        "median_remaining": int(median_remaining),
        "median_final": int(statistics.median(sorted(final_vals))),
        "n_comparables": len(remaining_vals),
        "basis": basis,
        "method": method,
        "days_on_sale": target_dos,
        "comparables": sorted(used, key=lambda c: (not c["same_series"], -c["final"])),
        "target_price": target_price,
        "price_band": (round(lo, 0), round(hi, 0)) if lo is not None else None,
        "surge_share": round(surge_share, 3),
        "surge_tickets": surge_tickets,
        "surge_window_days": SURGE_WINDOW_DAYS,
        "surge_ahead": bool(surge_ahead),
    }


# Industry benchmarks for impression → ticket-purchase conversion
# Sourced from Unbounce/Shopify 2026 landing page benchmarks + typical CTR assumptions.
# These represent IMPRESSION → PURCHASE (not landing-page → purchase). They account for the
# full funnel: impression → click → landing page → checkout.
IMPRESSION_CONVERSION = {
    "cold_paid_social":    0.001,   # 0.1% — cold IG/TikTok ads (1% CTR × ~10% paid LP CVR, weak match)
    "organic_social":      0.003,   # 0.3% — feed posts to existing followers (warm community)
    "warm_paid_social":    0.005,   # 0.5% — retargeting + lookalikes for engaged audiences
    "email_to_list":       0.020,   # 2.0% — email blasts to an opted-in community (22% open × 9% CVR)
    "blended_typical":     0.004,   # 0.4% — typical OTT-style blended mix
}


def impressions_to_target(current_sold: int, target: int, conversion_rate: float = IMPRESSION_CONVERSION["blended_typical"]) -> dict:
    """How many impressions are needed to close the gap from current_sold to target."""
    gap = max(0, target - current_sold)
    return {
        "gap": gap,
        "conversion_rate": conversion_rate,
        "impressions_needed": int(round(gap / conversion_rate)) if gap and conversion_rate > 0 else 0,
    }


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
