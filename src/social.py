"""Organic social-media post analytics — Instagram + TikTok organic.

Treats every post/video as an "impression source" for an event. Posts are matched to events
by caption-keyword rules (same regex table as ad-set attribution), then routed to the next
future event in that series (skipping events flagged `marketing_skip` in capacities.csv).

Snapshots refresh: ask Claude to re-pull `data/social_instagram.json` and `data/social_tiktok.json`
from Windsor MCP (connectors `instagram` + `tiktok_organic`).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.marketing import ADSET_RULES, _classify_adset, _event_series

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IG_SNAPSHOT = DATA_DIR / "social_instagram.json"
TT_SNAPSHOT = DATA_DIR / "social_tiktok.json"
APIFY_IG_SNAPSHOT = DATA_DIR / "apify_ig_mentions.json"
APIFY_TT_SNAPSHOT = DATA_DIR / "apify_tt_mentions.json"

POST_OVERRIDES_CSV = Path(__file__).resolve().parent.parent / "config" / "post_overrides.csv"

OWNED_HANDLES = {"overthetopxp"}
TEAM_HANDLES = {"thedandale", "kyleculus"}

# Past-tense / recap markers — strong signal a post is reviewing a past event, not promoting next.
RECAP_PHRASES = [
    r"thank\s*you\s*(to|for)",
    r"thanks?\s*(to|for)\s*(everyone|y'?all|you all|the team|coming)",
    r"this\s+(past|last)\s+(weekend|saturday|sunday|night|friday|month)",
    r"\bicymi\b",
    r"yall\s+(came|showed|brought|killed|did)",
    r"y'all\s+(came|showed|brought|killed|did)",
    r"we\s+just\s+(wrapped|did|had|finished|hosted)",
    r"(last|past)\s+night",
    r"\brecap\b",
    r"was\s+(amazing|incredible|epic|so much fun|a vibe|unreal|insane|wild)",
    r"what\s+a\s+(weekend|day|night|vibe|time)",
    r"shoutout to.*came",
    r"thanks\s+for\s+(coming|pulling|showing)",
    r"📸 by",  # photo credits = post-event recap pattern
    r"📷 by",
]
RECAP_RE = re.compile("|".join(RECAP_PHRASES), re.IGNORECASE)

# Forward-looking promo markers
PROMO_PHRASES = [
    r"\bsee\s+(you|ya|yall|y'?all)\b",
    r"\bjoin\s+us\b",
    r"\bpull\s*up\b",
    r"\btickets?\s+(live|available|moving|on sale|in bio|going fast)",
    r"\blink\s+in\s+bio\b",
    r"\bsave\s+the\s+date\b",
    r"\bdon'?t\s+miss\b",
    r"\bnext\s+(weekend|saturday|sunday|month|one)\b",
    r"\bthis\s+(weekend|saturday|sunday)\b",
    r"\bcoming\s+(up|soon)\b",
    r"\bRSVP\b",
    r"\bcomes back\b|\bis back\b|\breturns\b",
    r"\bearly\s*bird\b",
    r"\bget\s+(your|ya|yours)\s+ticket",
]
PROMO_RE = re.compile("|".join(PROMO_PHRASES), re.IGNORECASE)


def _extract_date_mentions(caption: str) -> list[tuple[int, int]]:
    """Find date references in a caption (month/day pairs). Returns list of (month, day)."""
    if not isinstance(caption, str):
        return []
    text = caption
    months = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
              "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
              "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
              "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
    found: list[tuple[int, int]] = []
    # Pattern A: "Jan 31", "January 31st", "Feb 21"
    for m in re.finditer(r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                         text, re.IGNORECASE):
        month = months.get(m.group(1).lower())
        day = int(m.group(2))
        if month and 1 <= day <= 31:
            found.append((month, day))
    # Pattern B: "1/31", "5/9", "2/21", "6/27"
    for m in re.finditer(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", text):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31:
            found.append((a, b))
    return found


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


def _classify_origin(owner: str | None, coauthors: list | None = None) -> str:
    """Classify a post into one of four buckets:

    - **owned**: posted by an OTT-controlled account with no external co-authors.
    - **team**: posted by an OTT founder's personal account (Gabriel / Kyle) — counts as owned
      for the dashboard summary but kept separate so we can tell internal-feed from team-feed.
    - **collab**: posted by an external account that includes @overthetopxp (or an OTT-team
      handle) as a co-author. Instagram surfaces these on BOTH accounts' feeds — they're a
      paid-partnership-style boost that's neither purely owned nor purely earned.
    - **earned**: external account with no OTT co-author. Pure community amplification.
    """
    coauthors = [c.lower() for c in (coauthors or []) if isinstance(c, str)]
    has_ott_coauthor = bool(set(coauthors) & (OWNED_HANDLES | TEAM_HANDLES))
    if not owner:
        return "earned"
    o = owner.lower()
    if o in OWNED_HANDLES:
        return "owned"
    if o in TEAM_HANDLES:
        return "team"
    if has_ott_coauthor:
        return "collab"
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
    # Pull coauthors list (Apify IG returns the field for collab posts)
    if "coauthorProducers" in df.columns:
        df["coauthors"] = df["coauthorProducers"].apply(lambda v: v if isinstance(v, list) else [])
    else:
        df["coauthors"] = [[] for _ in range(len(df))]
    df["origin"] = df.apply(lambda r: _classify_origin(r["owner"], r["coauthors"]), axis=1)
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


def load_post_overrides(path: Path = POST_OVERRIDES_CSV) -> dict[str, dict]:
    """Manual post→event overrides. CSV columns: post_id, instance_key, note.

    Set instance_key to "ignore" to drop a post from attribution entirely.
    """
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str, comment="#").fillna("")
    except pd.errors.EmptyDataError:
        return {}
    out = {}
    for _, r in df.iterrows():
        pid = (r.get("post_id") or "").strip()
        if not pid or pid.startswith("#"):
            continue
        out[pid] = {
            "instance_key": (r.get("instance_key") or "").strip(),
            "note": (r.get("note") or "").strip(),
        }
    return out


def _detect_ambiguity(p: pd.Series, target: pd.Series, ev: pd.DataFrame) -> tuple[bool, str, list[str]]:
    """Return (is_ambiguous, reason, alternative_instance_keys).

    A post is flagged ambiguous when EITHER:
      - It's clearly a recap of a past event but was routed forward to a future one
      - Its caption mentions a specific date that doesn't match the routed event's date
        (and DOES match another event in the same series)
    """
    caption = p.get("caption") or ""
    if not isinstance(caption, str):
        return (False, "", [])

    target_date = pd.to_datetime(target["event_instance_date"])
    post_date = pd.to_datetime(p["date"])
    series = target.get("series")

    # 1. Date-mention check: does the caption reference a specific date that doesn't match
    #    the routed event's date, but DOES match another event in the same series?
    same_series_events = ev[ev["series"] == series]
    mentions = _extract_date_mentions(caption)
    matched_event_keys: list[str] = []
    for (m, d) in mentions:
        if m == target_date.month and d == target_date.day:
            return (False, "", [])  # Caption explicitly confirms the routed event — not ambiguous
        for _, e in same_series_events.iterrows():
            edt = pd.to_datetime(e["event_instance_date"])
            if edt.month == m and edt.day == d and str(e["instance_key"]) != str(target["instance_key"]):
                matched_event_keys.append(str(e["instance_key"]))
    if matched_event_keys:
        return (True, f"Caption mentions a date that matches a different event in the {series} series",
                list(dict.fromkeys(matched_event_keys)))

    # 2. Recap-vs-promo check: post has strong recap language and was routed FORWARD
    is_recap = bool(RECAP_RE.search(caption))
    is_promo = bool(PROMO_RE.search(caption))
    days_diff = (target_date - post_date).days
    if is_recap and not is_promo and days_diff > 0:
        # Find the most recent past event in series — likely the actual subject
        past = same_series_events[same_series_events["event_instance_date"] < post_date]
        if not past.empty:
            recent = past.sort_values("event_instance_date").iloc[-1]
            if str(recent["instance_key"]) != str(target["instance_key"]):
                return (True, "Caption reads as a recap, but was routed to a future event",
                        [str(recent["instance_key"])])

    return (False, "", [])


def attribute_posts_to_events(posts: pd.DataFrame, tickets: pd.DataFrame,
                               capacities: pd.DataFrame | None = None) -> pd.DataFrame:
    """Map each post to a specific event using the same routing as ad-set attribution.

    Also annotates posts with `is_ambiguous`, `ambiguity_reason`, and `alt_instance_keys`
    so the dashboard can surface a review queue. Overrides from `config/post_overrides.csv`
    take precedence over auto-routing.
    """
    if posts.empty or tickets.empty:
        return pd.DataFrame()

    skip_keys = set()
    if capacities is not None and not capacities.empty and "marketing_skip" in capacities.columns:
        skip_series = capacities["marketing_skip"].astype(str).str.strip().str.lower()
        skip_keys = set(capacities.loc[skip_series.isin(("true", "1", "yes", "y")), "instance_key"].astype(str))

    overrides = load_post_overrides()

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
        # Manual override check first
        override = overrides.get(str(p.get("post_id", "")))
        if override:
            ik = override["instance_key"]
            if ik.lower() in ("ignore", "skip", "drop", ""):
                continue
            match = ev[ev["instance_key"].astype(str) == ik]
            if match.empty:
                continue  # bad override — drop
            target = match.iloc[0]
            series = target.get("series", p.get("target_series", ""))
            is_amb, reason, alts = (False, f"overridden: {override.get('note','')}", [])
        else:
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
            is_amb, reason, alts = _detect_ambiguity(p, target, ev)

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
            "is_ambiguous": bool(is_amb),
            "ambiguity_reason": reason,
            "alt_instance_keys": ",".join(alts),
            "is_overridden": bool(override),
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
        # Three buckets: owned (incl. team), collab (OTT co-author), earned (external only)
        ap = attributed_posts.copy()
        ap["bucket"] = ap["origin"].map({
            "owned": "owned", "team": "owned",
            "collab": "collab",
            "earned": "earned",
        }).fillna("earned")
        for ch in ("instagram_organic", "tiktok_organic"):
            for bucket in ("owned", "collab", "earned"):
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
    # Convenience totals per bucket across IG + TikTok
    for bucket in ("owned", "collab", "earned"):
        v_cols = [c for c in out.columns if c.endswith(f"_{bucket}_views")]
        p_cols = [c for c in out.columns if c.endswith(f"_{bucket}_posts")]
        out[f"organic_{bucket}_views"] = out[v_cols].sum(axis=1) if v_cols else 0
        out[f"organic_{bucket}_posts"] = (out[p_cols].sum(axis=1).astype(int)
                                          if p_cols else 0)

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
