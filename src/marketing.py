"""Ad-campaign analytics — pulls Windsor.ai-sourced Facebook ad data and attributes it to events
at the AD-SET level (not just campaign). Necessary because a single campaign like "P1 - 2025 -
Purchase Conversion - The Blend" actually runs ad sets for Tahoe, Yacht, Sacramento, SF, etc.

Refresh: ads data is captured to `data/ads_facebook.json` as a snapshot. To refresh, ask Claude
to re-pull from the Windsor MCP (`get_data` on connector="facebook") with adset_name field.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.analytics import _series_tokens, load_tickets

ADS_FB_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "ads_facebook.json"


# Ad-set name patterns → (series, event_name_substr_for_disambiguation_or_None).
# Matched in order; first hit wins. Series is the event-series token; the optional substring
# narrows to a specific event in that series (e.g. "Sacramento" + "blend" → Sac Blend).
# The fallback below handles generic ad sets ("the blend" → next "flagship" Blend, etc.).
# An ad/post only attributes to an event if it ran within this many days BEFORE the event.
# Stops ads for prior-year events (e.g. 2024/2025 Fit Fest campaigns that aren't in the
# SweatPals data) from being force-attributed forward to the next event in the dataset.
# OTT runs roughly monthly events and rarely promotes more than ~2 months out, so 120 days
# is a generous cap that still excludes the 270-500-day-old stale campaigns.
MAX_LOOKBACK_DAYS = 120

ADSET_RULES: list[tuple[str, str, str | None]] = [
    # Most specific first. The first matching rule wins, so the order matters.

    # --- Blend location-specific hints (caption mentions a city) ---
    (r"sacramento|#?\bsac\b",                    "blend",         "Sacramento"),
    (r"san\s*jose|#sanjose",                     "blend",         "San Jose"),
    (r"\bsf\b.*the\s*blend|the\s*blend.*\bsf\b|#?san\s*francisco.*coffee|coffee.*#?san\s*francisco",
                                                  "blend",         "San Francisco"),
    # "Black Joy" hint is for the Feb 21 Black Joy Weekend Blend only.
    # We do NOT match on "juneteenth" because in captions it refers to the actual June Juneteenth
    # holiday (the June 20 Blend), not Black Joy Weekend in February.
    (r"black\s*joy",                             "blend",         "Black Joy"),
    (r"after\s*party",                           "blend",         "After Party"),

    # --- Yacht ---
    (r"#ottyachtparty|#yachtparty|super\s*bowl\s*yacht|yacht\s*party|yacht",
                                                  "yacht",         None),
    (r"anniversary\s*yacht",                     "yacht",         None),

    # --- Fit Fest ---
    (r"super\s*bowl\s*fit\s*fest",               "fit fest",      "Super Bowl"),
    (r"world\s*cup",                             "fit fest",      "World Cup"),
    (r"#sffitfest|#fitfest|#fitnessfestival",    "fit fest",      None),
    (r"fit\s*fest|fitness\s*festival",           "fit fest",      None),

    # --- Tahoe ---
    (r"#tahoeunscripted|#otttakestahoe",         "tahoe",         None),
    (r"tahoe\s*ski|tahoe",                       "tahoe",         None),

    # --- Other event series ---
    (r"\bgolf\b",                                "golf",          None),
    (r"brunch.*build|build.*brunch|#brunchandbuild",
                                                  "brunch & build",None),
    (r"croatia",                                 "_croatia_",     None),
    (r"membership",                              "_membership_",  None),
    (r"pilates",                                 "pilates",       None),

    # --- Generic Blend keywords (hashtag-driven) ---
    (r"#theblend|#blendcoffee|#coffeeandrnb|#coffeernb",
                                                  "blend",         None),
    # Both #coffee AND #rnb (strong combined signal for Blend) — both hashtags must be present
    (r"(?=.*#coffee)(?=.*#rnb)",                 "blend",         None),
    # Phrase "coffee and r&b" or variants
    (r"coffee\s*(and|&|\+)\s*r&?b",              "blend",         None),
    # Last fallback: literal "the blend" / "^blend" mention
    (r"the\s*blend|^blend",                      "blend",         None),
]


def load_ads(snapshot_path: Path = ADS_FB_SNAPSHOT) -> pd.DataFrame:
    """Load FB ad data with ad-set granularity."""
    if not snapshot_path.exists():
        return pd.DataFrame(columns=["campaign", "adset_name", "date", "impressions", "spend", "clicks", "reach"])
    raw = json.loads(snapshot_path.read_text())
    df = pd.DataFrame(raw.get("result", []))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], utc=True)
    for col in ("impressions", "spend", "clicks", "reach"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "adset_name" not in df.columns:
        df["adset_name"] = ""
    df["adset_name"] = df["adset_name"].fillna("").astype(str)
    # Match each ad row to a (series, name_hint) rule
    df[["target_series", "target_name_hint"]] = df["adset_name"].apply(
        lambda s: pd.Series(_classify_adset(s))
    )
    # If ad-set didn't match anything, fall back to campaign name
    fallback_mask = df["target_series"].isna()
    if fallback_mask.any():
        df.loc[fallback_mask, "target_series"] = df.loc[fallback_mask, "campaign"].apply(
            lambda c: _classify_adset(c or "")[0]
        )
    df["campaign_series"] = df["target_series"]  # back-compat
    return df


def _classify_adset(name: str) -> tuple[str | None, str | None]:
    n = (name or "").lower()
    if not n:
        return (None, None)
    if "instagram post:" in n:
        return (None, None)
    for pattern, series, name_hint in ADSET_RULES:
        if re.search(pattern, n):
            return (series, name_hint)
    return (None, None)


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


def _lookback_map(capacities: pd.DataFrame | None) -> dict[str, int]:
    """Per-event lookback override (capacities.csv column `lookback_days`). Falls back to
    MAX_LOOKBACK_DAYS. Lets a long pre-sale event (e.g. Tahoe ~6 months out) count earlier ads."""
    out: dict[str, int] = {}
    if capacities is not None and not capacities.empty and "lookback_days" in capacities.columns:
        for _, r in capacities.iterrows():
            v = pd.to_numeric(r.get("lookback_days"), errors="coerce")
            if pd.notna(v):
                out[str(r["instance_key"])] = int(v)
    return out


def attribute_ads_to_events(ads: pd.DataFrame, tickets: pd.DataFrame,
                            capacities: pd.DataFrame | None = None,
                            waitlist: pd.DataFrame | None = None) -> pd.DataFrame:
    """For each ad-day, attribute spend/impressions to a specific event.

    Resolution order (per ad-day):
      1. Ad-set rule with name_hint → match a specific event by substring (e.g.
         "Sacramento" hint → "Sacramento" in event_name)
      2. Ad-set rule with no name_hint OR no rule but campaign hints series →
         next FUTURE event in series within the lookback window, not `marketing_skip`
      3. No event within the lookback window → ad was for a prior event not in the data → skip

    Lookback defaults to MAX_LOOKBACK_DAYS but can be overridden per-event via the
    `lookback_days` column in capacities.csv. Waitlist-only events (e.g. an event on
    pre-sale before tickets open) are included so ads can attribute to them.
    """
    if ads.empty or tickets.empty:
        return pd.DataFrame()

    from src.analytics import event_index  # local import to avoid circularity

    skip_keys = set()
    if capacities is not None and not capacities.empty and "marketing_skip" in capacities.columns:
        skip_series = capacities["marketing_skip"].astype(str).str.strip().str.lower()
        skip_keys = set(capacities.loc[skip_series.isin(("true", "1", "yes", "y")), "instance_key"].astype(str))
    lookback_map = _lookback_map(capacities)

    # Event universe = ticket events + waitlist-only events; include address for location hints
    ev = event_index(tickets, waitlist).copy()
    ev["series"] = ev["event_name"].apply(_event_series)
    ev = ev.dropna(subset=["series", "event_instance_date"]).sort_values("event_instance_date")
    ev["search_text"] = (ev["event_name"].fillna("") + " " + ev["event_address_name"].fillna("")).str.lower()
    ev["lookback"] = ev["instance_key"].astype(str).map(lookback_map).fillna(MAX_LOOKBACK_DAYS)

    rows = []
    for _, ad in ads.iterrows():
        series = ad.get("target_series") or ad.get("campaign_series")
        if not series:
            continue
        name_hint = ad.get("target_name_hint")

        # 1. If a name hint is set, try to match a specific event in the series.
        # Hint matches against event_name + event_address_name (e.g. "Sacramento" is in the
        # venue address but not the event name). Hint matches bypass the marketing_skip filter
        # so an explicit caption like "San Jose coffee and r&b" still routes to San Jose Blend
        # even though San Jose is normally excluded from generic Blend attribution.
        ad_date = ad["date"]
        days_to = (ev["event_instance_date"] - ad_date).dt.days
        within_window = (days_to >= 0) & (days_to <= ev["lookback"])
        target = None
        if name_hint is not None and pd.notna(name_hint) and isinstance(name_hint, str) and name_hint:
            hint_lower = name_hint.lower()
            hint_match = ev["search_text"].str.contains(hint_lower, regex=False, na=False)
            matches = ev[(ev["series"] == series) & hint_match]
            if not matches.empty:
                future = ev[(ev["series"] == series) & hint_match & within_window]
                target = future.iloc[0] if not future.empty else matches.iloc[-1]

        # 2. Default: next future event in series within the lookback window
        if target is None:
            future = ev[(ev["series"] == series) & within_window &
                        (~ev["instance_key"].astype(str).isin(skip_keys))]
            if not future.empty:
                target = future.iloc[0]
            else:
                # No event within the window → this ad ran for a prior event not in the data. Skip.
                continue

        rows.append({
            "campaign": ad["campaign"],
            "adset_name": ad.get("adset_name", ""),
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


def current_organic_pace(attributed_posts: pd.DataFrame, instance_key: str, days: int = 14) -> dict:
    """Organic post views + post count per week for an event over the last N days."""
    empty = {"weekly_views": 0, "posts": 0, "daily_views": 0}
    if attributed_posts is None or attributed_posts.empty:
        return empty
    e = attributed_posts[attributed_posts["instance_key"].astype(str) == str(instance_key)].copy()
    if e.empty:
        return empty
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    recent = e[e["date"] >= cutoff]
    if recent.empty:
        return {**empty, "posts": 0}
    total_views = int(recent["views"].sum())
    return {
        "weekly_views": round(total_views / max(1, days) * 7),
        "daily_views": round(total_views / max(1, days)),
        "posts": int(len(recent)),
        "posts_per_week": round(len(recent) / max(1, days) * 7, 1),
    }
