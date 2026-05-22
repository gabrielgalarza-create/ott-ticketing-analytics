"""Ad-campaign analytics — pulls Windsor.ai-sourced Facebook ad data and attributes it to events.

Refresh: ads data is captured to `data/ads_facebook.json` as a snapshot. To refresh, ask Claude
to re-pull from the Windsor MCP (`get_data` on connector="facebook") and overwrite the snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.analytics import _series_tokens, load_tickets

ADS_FB_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "ads_facebook.json"


# Campaign-name keywords → series-token override (when name doesn't trivially match event names)
CAMPAIGN_SERIES_KEYWORDS = {
    "blend": "blend",
    "fit fest": "fit fest",
    "tahoe": "tahoe",
    "yacht": "yacht",
    "brunch and build": "brunch & build",
    "brunch & build": "brunch & build",
    "ott membership": "_membership_",
    "croatia": "_croatia_",
    "scottsdale": "_scottsdale_",
    "puerto rico": "_puerto_rico_",
    "tahoeunscripted": "tahoe",
    "anniversary": "yacht",
}


def load_ads(snapshot_path: Path = ADS_FB_SNAPSHOT) -> pd.DataFrame:
    """Load FB ad data and return a tidy DataFrame: campaign, date, impressions, spend, clicks, reach."""
    if not snapshot_path.exists():
        return pd.DataFrame(columns=["campaign", "date", "impressions", "spend", "clicks", "reach"])
    raw = json.loads(snapshot_path.read_text())
    df = pd.DataFrame(raw.get("result", []))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], utc=True)
    for col in ("impressions", "spend", "clicks", "reach"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["campaign_series"] = df["campaign"].apply(_campaign_series)
    return df


def _campaign_series(name: str) -> str | None:
    """Return a normalized series-tag for a campaign, e.g. 'blend', 'fit fest', 'brunch & build',
    or None if the campaign is generic / non-event."""
    n = (name or "").lower()
    for keyword, tag in CAMPAIGN_SERIES_KEYWORDS.items():
        if keyword in n:
            return tag
    # Skip generic Instagram-post-style campaigns
    if n.startswith("instagram post:"):
        return None
    return None


def _event_series(event_name) -> str | None:
    if not isinstance(event_name, str):
        return None
    n = event_name.lower()
    if "blend" in n:
        return "blend"
    if "fit fest" in n:
        return "fit fest"
    if "tahoe" in n:
        return "tahoe"
    if "yacht" in n:
        return "yacht"
    if "brunch" in n or "build" in n:
        return "brunch & build"
    if "membership" in n:
        return "_membership_"
    return None


def attribute_ads_to_events(ads: pd.DataFrame, tickets: pd.DataFrame) -> pd.DataFrame:
    """For each ad-day, attribute spend/impressions to the NEXT future event in that series.
    Returns a DataFrame: campaign, date, event_name, event_instance_date, event_instance_key,
    series, impressions, spend, clicks, reach.
    """
    if ads.empty or tickets.empty:
        return pd.DataFrame()

    # Build event index: one row per event-instance with its series + date
    ev = tickets.groupby("instance_key", as_index=False).agg(
        event_name=("event_name", "last"),
        event_instance_date=("event_instance_date", "last"),
        event_base_id=("event_base_id", "last"),
    )
    ev["series"] = ev["event_name"].apply(_event_series)
    ev = ev.dropna(subset=["series", "event_instance_date"]).sort_values("event_instance_date")

    rows = []
    for _, ad in ads.iterrows():
        series = ad["campaign_series"]
        if not series:
            continue
        # Find the next event in that series whose date is on/after this ad day
        candidates = ev[(ev["series"] == series) & (ev["event_instance_date"] >= ad["date"])]
        if candidates.empty:
            # No future event — attribute to the most recent past event in series
            candidates = ev[ev["series"] == series]
            if candidates.empty:
                continue
            target = candidates.iloc[-1]
        else:
            target = candidates.iloc[0]
        rows.append({
            "campaign": ad["campaign"],
            "date": ad["date"],
            "instance_key": target["instance_key"],
            "event_name": target["event_name"],
            "event_instance_date": target["event_instance_date"],
            "series": series,
            "impressions": ad["impressions"],
            "spend": ad["spend"],
            "clicks": ad["clicks"],
            "reach": ad["reach"],
        })
    return pd.DataFrame(rows)


def event_marketing_summary(attributed: pd.DataFrame) -> pd.DataFrame:
    """Per-event aggregates of marketing spend + impressions."""
    if attributed.empty:
        return pd.DataFrame()
    g = attributed.groupby(["instance_key", "event_name", "event_instance_date"], as_index=False).agg(
        impressions=("impressions", "sum"),
        spend=("spend", "sum"),
        clicks=("clicks", "sum"),
        reach=("reach", "sum"),
        ad_days=("date", "nunique"),
        first_ad_date=("date", "min"),
        last_ad_date=("date", "max"),
    )
    g["ctr"] = (g["clicks"] / g["impressions"]).where(g["impressions"] > 0)
    g["cpm"] = (g["spend"] / g["impressions"] * 1000).where(g["impressions"] > 0)
    return g


def event_marketing_table(tickets: pd.DataFrame, attributed_summary: pd.DataFrame) -> pd.DataFrame:
    """Join marketing summary with ticket counts. Returns the headline table: per event,
    impressions / tickets / spend / efficiency."""
    if tickets.empty:
        return pd.DataFrame()
    ticket_counts = tickets.groupby("instance_key").agg(
        tickets_sold=("id", "count"),
        revenue=("net_price", "sum"),
    ).reset_index()
    if attributed_summary.empty:
        merged = ticket_counts.copy()
        for col in ("impressions", "spend", "clicks", "reach", "ad_days", "ctr", "cpm"):
            merged[col] = 0 if col != "ctr" and col != "cpm" else float("nan")
    else:
        merged = ticket_counts.merge(
            attributed_summary, on="instance_key", how="left"
        ).fillna({"impressions": 0, "spend": 0, "clicks": 0, "reach": 0, "ad_days": 0})

    # Need event_name + date
    meta = tickets.sort_values("order_date").groupby("instance_key", as_index=False).agg(
        event_name=("event_name", "last"),
        event_instance_date=("event_instance_date", "last"),
    )
    merged = merged.merge(meta, on="instance_key", how="left", suffixes=("", "_meta"))
    # Fill any missing event_name (events with no ads) from the meta join
    if "event_name_meta" in merged.columns:
        merged["event_name"] = merged.get("event_name", pd.Series([None]*len(merged))).fillna(merged["event_name_meta"])
        merged["event_instance_date"] = merged.get("event_instance_date", pd.Series([None]*len(merged))).fillna(merged["event_instance_date_meta"])
        merged = merged.drop(columns=[c for c in ("event_name_meta", "event_instance_date_meta") if c in merged.columns])
    # Efficiency metrics
    merged["impressions_per_ticket"] = (merged["impressions"] / merged["tickets_sold"]).where(merged["tickets_sold"] > 0)
    merged["cpa"] = (merged["spend"] / merged["tickets_sold"]).where(merged["tickets_sold"] > 0)
    merged["roas"] = (merged["revenue"] / merged["spend"]).where(merged["spend"] > 0)
    return merged.sort_values("event_instance_date")


def recommended_daily_spend(event_summary_row: pd.Series,
                            marketing_table: pd.DataFrame,
                            target_tickets: int,
                            days_left: int) -> dict:
    """Recommend daily impressions/spend to close the gap to target, anchored to past
    same-series events' actual impressions-per-ticket ratio."""
    if days_left <= 0:
        return {}
    series = _event_series(event_summary_row["event_name"])
    if series is None:
        return {}

    # Find past same-series events with impressions data
    past_same_series = marketing_table[
        (marketing_table["event_name"].apply(lambda n: _event_series(n) == series)) &
        (marketing_table["instance_key"] != event_summary_row["instance_key"]) &
        (marketing_table["impressions"] > 0) &
        (marketing_table["impressions_per_ticket"].notna())
    ]
    if past_same_series.empty:
        # Fallback to ALL events with impressions data
        past_same_series = marketing_table[
            (marketing_table["instance_key"] != event_summary_row["instance_key"]) &
            (marketing_table["impressions"] > 0) &
            (marketing_table["impressions_per_ticket"].notna())
        ]
    if past_same_series.empty:
        return {}

    # Use median for robustness
    median_imp_per_tix = past_same_series["impressions_per_ticket"].median()
    median_cpa = past_same_series["cpa"].median()
    current_sold = event_summary_row["tickets_sold"]
    gap = max(0, target_tickets - current_sold)

    return {
        "median_impressions_per_ticket": float(median_imp_per_tix),
        "median_cpa": float(median_cpa) if pd.notna(median_cpa) else None,
        "gap_tickets": int(gap),
        "total_impressions_needed": int(round(gap * median_imp_per_tix)),
        "daily_impressions_needed": int(round(gap * median_imp_per_tix / days_left)),
        "total_spend_needed": float(gap * median_cpa) if pd.notna(median_cpa) else None,
        "daily_spend_needed": float(gap * median_cpa / days_left) if pd.notna(median_cpa) else None,
        "past_events_used": int(len(past_same_series)),
        "series": series,
    }


def current_pace(attributed: pd.DataFrame, instance_key: str, days: int = 7) -> dict:
    """Average impressions/spend per day over the last N days of campaigns for this event."""
    if attributed.empty:
        return {"daily_impressions": 0, "daily_spend": 0}
    e = attributed[attributed["instance_key"] == instance_key].copy()
    if e.empty:
        return {"daily_impressions": 0, "daily_spend": 0}
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    recent = e[e["date"] >= cutoff]
    if recent.empty:
        return {"daily_impressions": 0, "daily_spend": 0, "n_days": 0}
    daily = recent.groupby(recent["date"].dt.date).agg(
        impressions=("impressions", "sum"), spend=("spend", "sum")
    )
    return {
        "daily_impressions": float(daily["impressions"].mean()),
        "daily_spend": float(daily["spend"].mean()),
        "n_days": len(daily),
    }
