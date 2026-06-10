#!/usr/bin/env python3
"""Pull fresh marketing snapshots from Windsor.ai into data/ before the dashboard build.

Wired into .github/workflows/deploy.yml so CI no longer ships stale data. Needs the repo
secret WINDSOR_API_KEY set in GitHub Actions → Settings → Secrets and variables → Actions.

The script soft-fails: if the API key is missing or a single connector errors, it logs the
issue and keeps the existing committed snapshot for that connector. The build still goes out
with the most recent good data rather than breaking the deploy.

Run locally:
    WINDSOR_API_KEY=... python scripts/refresh_marketing_data.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
BASE = "https://connectors.windsor.ai"
DATE_PRESET = "last_2yearsT"
TIMEOUT = 180
MAX_TRIES = 3

# Each connector's REST slug + the fields the dashboard's loaders expect. Keep these field
# lists in lockstep with src/marketing.py:load_ads and src/social.py:{load_ig_posts,load_tiktok_posts}.
CONNECTORS = [
    {
        "name": "Facebook ads",
        "slug": "facebook",
        "fields": ["campaign", "adset_name", "date", "impressions", "spend", "clicks", "reach"],
        "out": DATA / "ads_facebook.json",
    },
    {
        "name": "Instagram organic",
        "slug": "instagram",
        "fields": ["media_id", "timestamp", "media_caption", "media_views", "media_reach",
                   "media_like_count", "media_comments_count", "media_permalink",
                   "media_shortcode", "media_product_type"],
        "out": DATA / "social_instagram.json",
    },
    {
        "name": "TikTok organic",
        "slug": "tiktok_organic",
        "fields": ["video_id", "video_create_datetime", "video_caption", "video_views_count",
                   "video_reach", "video_likes", "video_comments", "video_shares", "video_share_url"],
        "out": DATA / "social_tiktok.json",
    },
]


def fetch(connector: dict, api_key: str) -> list[dict]:
    """Hit Windsor REST API, return the row list. Retries on timeout / 5xx (Meta's API is
    flaky with 2-year date ranges and the upstream timeout cascades through Windsor)."""
    params = {
        "api_key": api_key,
        "fields": ",".join(connector["fields"]),
        "date_preset": DATE_PRESET,
    }
    url = f"{BASE}/{connector['slug']}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    last_exc: Exception | None = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read())
            # Windsor's REST docs say `data`, but the MCP returns `result`. Accept either.
            return payload.get("data") or payload.get("result") or []
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == MAX_TRIES:
                raise
            last_exc = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == MAX_TRIES:
                raise
            last_exc = e
        backoff = 5 * attempt  # 5s, 10s
        print(f"[refresh-marketing]   {connector['name']}: attempt {attempt} failed ({last_exc}) — retrying in {backoff}s")
        time.sleep(backoff)
    return []  # unreachable


def main() -> int:
    key = os.environ.get("WINDSOR_API_KEY", "").strip()
    if not key:
        print("[refresh-marketing] WINDSOR_API_KEY not set — keeping existing snapshots.")
        return 0

    any_failed = False
    for c in CONNECTORS:
        try:
            rows = fetch(c, key)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[refresh-marketing] {c['name']}: FAILED ({e}) — keeping existing snapshot")
            any_failed = True
            continue
        if not rows:
            print(f"[refresh-marketing] {c['name']}: API returned 0 rows — keeping existing snapshot")
            any_failed = True
            continue
        # Loaders all read raw["result"], so write that shape regardless of what the API used.
        c["out"].write_text(json.dumps({"result": rows}, separators=(",", ":")))
        print(f"[refresh-marketing] {c['name']}: wrote {len(rows):,} rows -> {c['out'].name}")

    # Soft-fail across the board: if anything broke we want CI to keep going with stale data
    # rather than block the deploy. Exit 0 either way; partial freshness beats no dashboard.
    return 0


if __name__ == "__main__":
    sys.exit(main())
