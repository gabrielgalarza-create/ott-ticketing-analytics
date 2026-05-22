"""Organic social-media post analytics — Instagram + TikTok organic.

Treats every post/video as an "impression source" for an event. Posts are matched to events
by caption-keyword rules (same regex table as ad-set attribution), then routed to the next
future event in that series (skipping events flagged `marketing_skip` in capacities.csv).

Snapshots refresh: ask Claude to re-pull `data/social_instagram.json` and `data/social_tiktok.json`
from Windsor MCP (connectors `instagram` + `tiktok_organic`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.marketing import ADSET_RULES, _classify_adset, _event_series

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IG_SNAPSHOT = DATA_DIR / "social_instagram.json"
TT_SNAPSHOT = DATA_DIR / "social_tiktok.json"
# Apify-scraped data: includes external mentions tagging @overthetopxp + recent
# OTT posts pulled directly from IG/TikTok with engagement metrics not in Windsor.
APIFY_IG_SNAPSHOT = DATA_DIR / "apify_ig_mentions.json"
APIFY_TT_SNAPSHOT = DATA_DIR / "apify_tt_mentions.json"

# Owners considered "OTT-owned" (vs earned). Everything else is earned media.
OWNED_HANDLES = {"overthetopxp"}
TEAM_HANDLES = {"thedandale", "kyleculus"}  # OTT founders' personal accounts


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return raw.get("result", []) or []


def load_ig_posts(path: Path = IG_SNAPSHOT) -> pd.DataFrame:
    rows = _load_rows(path)
    if not rows:
        return pd.DataFrame(columns=["channel", "post_id", "date", "caption", "views", "reach",
                                     "likes", "comments", "shares", "url", "media_type"])
    df = pd.DataFrame(rows)
    df["channel"] = "instagram_organic"
    df["post_id"] = df["media_id"].astype(str)
    df["date"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["caption"] = df["media_caption"].fillna("").astype(str)
    df["views"] = pd.to_numeric(df.get("media_views"), errors="coerce").fillna(0).astype(int)
    df["reach"] = pd.to_numeric(df.get("media_reach"), errors="coerce").fillna(0).astype(int)
    df["likes"] = pd.to_numeric(df.get("media_like_count"), errors="coerce").fillna(0).astype(int)
    df["comments"] = pd.to_numeric(df.get("media_comments_count"), errors="coerce").fillna(0).astype(int)
    df["shares"] = 0
    df["url"] = df.get("media_permalink", "")
    df["media_type"] = df.get("media_product_type", "")
    return df[["channel", "post_id", "date", "caption", "views", "reach", "likes", "comments", "shares", "url", "media_type"]]


def load_tiktok_posts(path: Path = TT_SNAPSHOT) -> pd.DataFrame:
    rows = _load_rows(path)
    if not rows:
        return pd.DataFrame(columns=["channel", "post_id", "date", "caption", "views", "reach",
                                     "likes", "comments", "shares", "url", "media_type"])
    df = pd.DataFrame(rows)
    df["channel"] = "tiktok_organic"
    df["post_id"] = df["video_id"].astype(str)
    df["date"] = pd.to_datetime(df["video_create_datetime"], utc=True, errors="coerce")
    df["caption"] = df["video_caption"].fillna("").astype(str)
    df["views"] = pd.to_numeric(df.get("video_views_count"), errors="coerce").fillna(0).astype(int)
    df["reach"] = pd.to_numeric(df.get("video_reach"), errors="coerce").fillna(0).astype(int)
    df["likes"] = pd.to_numeric(df.get("video_likes"), errors="coerce").fillna(0).astype(int)
    df["comments"] = pd.to_numeric(df.get("video_comments"), errors="coerce").fillna(0).astype(int)
    df["shares"] = pd.to_numeric(df.get("video_shares"), errors="coerce").fillna(0).astype(int)
    df["url"] = df.get("video_share_url", "")
    df["media_type"] = "VIDEO"
    return df[["channel", "post_id", "date", "caption", "views", "reach", "likes", "comments", "shares", "url", "media_type"]]


def _classify_origin(owner: str | None) -> str:
    """OTT-owned, team personal, or earned media."""
    if not owner:
        return "earned"
    o = owner.lower()
    if o in OWNED_HANDLES:
        return "owned"
    if o in TEAM_HANDLES:
        return "team"
    return "earned"


def load_apify_ig() -> pd.DataFrame:
    """Apify Instagram scrape — includes external accounts tagging @overthetopxp."""
    rows = _load_rows(APIFY_IG_SNAPSHOT)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["channel"] = "instagram_organic"
    df["post_id"] = df["id"].astype(str)
    df["date"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["caption"] = df["caption"].fillna("").astype(str)
    # Apify provides videoPlayCount (raw plays incl. replays) for videos and likesCount for posts.
    # Use videoPlayCount as the "views" metric for video posts, else likesCount*~10 as a rough
    # impression proxy for image posts (since Apify doesn't give impressions for static posts).
    play = pd.to_numeric(df.get("videoPlayCount"), errors="coerce").fillna(0)
    likes = pd.to_numeric(df.get("likesCount"), errors="coerce").fillna(0).clip(lower=0)
    df["views"] = play.where(play > 0, (likes * 10).astype(int)).astype(int)
    df["reach"] = pd.to_numeric(df.get("videoViewCount"), errors="coerce").fillna(0).astype(int)
    df["likes"] = likes.astype(int)
    df["comments"] = pd.to_numeric(df.get("commentsCount"), errors="coerce").fillna(0).clip(lower=0).astype(int)
    df["shares"] = 0
    df["url"] = df.get("url", "")
    df["media_type"] = df.get("type", "")
    df["owner"] = df.get("ownerUsername", "").fillna("").astype(str)
    df["origin"] = df["owner"].apply(_classify_origin)
    df["source"] = "apify"
    return df[["channel", "post_id", "date", "caption", "views", "reach", "likes",
               "comments", "shares", "url", "media_type", "owner", "origin", "source"]]


def load_apify_tt() -> pd.DataFrame:
    """Apify TikTok hashtag scrape (filtered to OTT-relevant items only)."""
    rows = _load_rows(APIFY_TT_SNAPSHOT)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["channel"] = "tiktok_organic"
    df["post_id"] = df["webVideoUrl"].astype(str).str.extract(r"/video/(\d+)")[0]
    df["date"] = pd.to_datetime(df["createTimeISO"], utc=True, errors="coerce")
    df["caption"] = df["text"].fillna("").astype(str)
    df["views"] = pd.to_numeric(df["playCount"], errors="coerce").fillna(0).astype(int)
    df["reach"] = df["views"]
    df["likes"] = pd.to_numeric(df["diggCount"], errors="coerce").fillna(0).astype(int)
    df["comments"] = pd.to_numeric(df["commentCount"], errors="coerce").fillna(0).astype(int)
    df["shares"] = pd.to_numeric(df["shareCount"], errors="coerce").fillna(0).astype(int)
    df["url"] = df["webVideoUrl"]
    df["media_type"] = "VIDEO"
    df["owner"] = df["authorMeta.name"].fillna("").astype(str) if "authorMeta.name" in df.columns else ""
    df["origin"] = df["owner"].apply(_classify_origin)
    df["source"] = "apify"
    return df[["channel", "post_id", "date", "caption", "views", "reach", "likes",
               "comments", "shares", "url", "media_type", "owner", "origin", "source"]]


def load_all_posts() -> pd.DataFrame:
    """Concat IG + TikTok posts from Windsor + Apify into one unified DataFrame.
    Apify rows win on de-dupe (post_id) since they're fresher and include earned media."""
    windsor_ig = load_ig_posts()
    windsor_tt = load_tiktok_posts()
    apify_ig = load_apify_ig()
    apify_tt = load_apify_tt()

    # Windsor posts are OTT-owned (they come from OTT's own accounts)
    for w in (windsor_ig, windsor_tt):
        if not w.empty:
            w["owner"] = "overthetopxp"
            w["origin"] = "owned"
            w["source"] = "windsor"

    frames = [f for f in (windsor_ig, windsor_tt, apify_ig, apify_tt) if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    # De-dupe: prefer Apify (richer engagement data) over Windsor for the same post
    df = df.sort_values("source").drop_duplicates(subset=["channel", "post_id"], keep="first")
    df["caption"] = df["caption"].fillna("").astype(str)

    # Caption-based event series classification (same rules as ad sets)
    df[["target_series", "target_name_hint"]] = df["caption"].apply(
        lambda s: pd.Series(_classify_adset(s))
    )
    return df.reset_index(drop=True)


def attribute_posts_to_events(posts: pd.DataFrame, tickets: pd.DataFrame,
                               capacities: pd.DataFrame | None = None) -> pd.DataFrame:
    """Map each post to a specific event using the same routing as ad-set attribution."""
    if posts.empty or tickets.empty:
        return pd.DataFrame()

    skip_keys = set()
    if capacities is not None and not capacities.empty and "marketing_skip" in capacities.columns:
        skip_series = capacities["marketing_skip"].astype(str).str.strip().str.lower()
        skip_keys = set(capacities.loc[skip_series.isin(("true", "1", "yes", "y")), "instance_key"].astype(str))

    ev = tickets.groupby("instance_key", as_index=False).agg(
        event_name=("event_name", "last"),
        event_address_name=("event_address_name", "last"),
        event_instance_date=("event_instance_date", "last"),
        event_base_id=("event_base_id", "last"),
    )
    ev["series"] = ev["event_name"].apply(_event_series)
    ev = ev.dropna(subset=["series", "event_instance_date"]).sort_values("event_instance_date")
    ev["search_text"] = (ev["event_name"].fillna("") + " " + ev["event_address_name"].fillna("")).str.lower()

    rows = []
    for _, p in posts.iterrows():
        series = p.get("target_series")
        if not isinstance(series, str) or not series:
            continue
        name_hint = p.get("target_name_hint")

        target = None
        if name_hint is not None and pd.notna(name_hint) and isinstance(name_hint, str) and name_hint:
            hint_lower = name_hint.lower()
            same_series = ev[ev["series"] == series]
            matches = same_series[same_series["search_text"].str.contains(hint_lower, regex=False, na=False)]
            if not matches.empty:
                future = matches[matches["event_instance_date"] >= p["date"]]
                target = future.iloc[0] if not future.empty else matches.iloc[-1]

        if target is None:
            future = ev[(ev["series"] == series) &
                        (ev["event_instance_date"] >= p["date"]) &
                        (~ev["instance_key"].astype(str).isin(skip_keys))]
            if not future.empty:
                target = future.iloc[0]
            else:
                past = ev[(ev["series"] == series) &
                          (~ev["instance_key"].astype(str).isin(skip_keys))]
                if past.empty:
                    continue
                target = past.iloc[-1]

        rows.append({
            "channel": p["channel"],
            "post_id": p["post_id"],
            "date": p["date"],
            "caption": p["caption"][:240],
            "views": int(p["views"]),
            "reach": int(p["reach"]),
            "likes": int(p["likes"]),
            "comments": int(p["comments"]),
            "shares": int(p["shares"]),
            "url": p["url"],
            "media_type": p["media_type"],
            "owner": p.get("owner", ""),
            "origin": p.get("origin", "earned"),
            "source": p.get("source", "windsor"),
            "instance_key": target["instance_key"],
            "event_name": target["event_name"],
            "event_instance_date": target["event_instance_date"],
            "series": series,
        })
    return pd.DataFrame(rows)


def unified_marketing_summary(tickets: pd.DataFrame, attributed_ads: pd.DataFrame,
                              attributed_posts: pd.DataFrame) -> pd.DataFrame:
    """Per-event total impressions/views by channel + grand total."""
    pieces = []
    if not attributed_ads.empty:
        a = attributed_ads.groupby(["instance_key"], as_index=False).agg(
            paid_impressions=("impressions", "sum"),
            paid_spend=("spend", "sum"),
        )
        pieces.append(a)
    if not attributed_posts.empty:
        # Group all owned (including team) together as "owned", earned separately.
        # That way the dashboard can show 3 buckets: paid (FB ads) / owned / earned.
        ap = attributed_posts.copy()
        ap["bucket"] = ap["origin"].map({"owned": "owned", "team": "owned"}).fillna("earned")
        for ch in ("instagram_organic", "tiktok_organic"):
            for bucket in ("owned", "earned"):
                sub = ap[(ap["channel"] == ch) & (ap["bucket"] == bucket)]
                if sub.empty:
                    continue
                g = sub.groupby(["instance_key"], as_index=False).agg(
                    **{f"{ch}_{bucket}_views": ("views", "sum"),
                       f"{ch}_{bucket}_posts": ("post_id", "count")},
                )
                pieces.append(g)

    if not pieces:
        return pd.DataFrame()

    out = pieces[0]
    for p in pieces[1:]:
        out = out.merge(p, on="instance_key", how="outer")
    out = out.fillna(0)

    # Total impressions across all channels (paid + organic views)
    impressions_cols = [c for c in out.columns if c.endswith("_impressions") or c.endswith("_views")]
    out["total_impressions"] = out[impressions_cols].sum(axis=1)
    # Convenience totals: organic owned + organic earned across IG + TikTok
    owned_cols = [c for c in out.columns if c.endswith("_owned_views")]
    earned_cols = [c for c in out.columns if c.endswith("_earned_views")]
    out["organic_owned_views"] = out[owned_cols].sum(axis=1) if owned_cols else 0
    out["organic_earned_views"] = out[earned_cols].sum(axis=1) if earned_cols else 0
    owned_post_cols = [c for c in out.columns if c.endswith("_owned_posts")]
    earned_post_cols = [c for c in out.columns if c.endswith("_earned_posts")]
    out["organic_owned_posts"] = out[owned_post_cols].sum(axis=1).astype(int) if owned_post_cols else 0
    out["organic_earned_posts"] = out[earned_post_cols].sum(axis=1).astype(int) if earned_post_cols else 0

    # Add event metadata
    if not tickets.empty:
        meta = tickets.sort_values("order_date").groupby("instance_key", as_index=False).agg(
            event_name=("event_name", "last"),
            event_instance_date=("event_instance_date", "last"),
            tickets_sold=("id", "count"),
            revenue=("net_price", "sum"),
        )
        out = out.merge(meta, on="instance_key", how="left")
        out["impressions_per_ticket"] = (out["total_impressions"] / out["tickets_sold"]).where(out["tickets_sold"] > 0)
    return out.sort_values("event_instance_date") if "event_instance_date" in out.columns else out


def top_posts_for_event(attributed_posts: pd.DataFrame, instance_key: str, n: int = 5) -> pd.DataFrame:
    """Top N posts (by views) attributed to a given event."""
    if attributed_posts.empty:
        return pd.DataFrame()
    sub = attributed_posts[attributed_posts["instance_key"] == instance_key].copy()
    if sub.empty:
        return sub
    return sub.sort_values("views", ascending=False).head(n)
