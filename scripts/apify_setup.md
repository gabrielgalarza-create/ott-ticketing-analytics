# Apify cross-platform mention scraping — setup

You want to track mentions of OTT events (hashtags like `#TheBlend`, `#SFFitFest`, `#OTTYachtParty`,
plus `@overthetopxp` mentions) across platforms beyond your own posts. Here's the setup that hooks
straight into this dashboard via Windsor's `apify_dataset` connector — no separate API key needed.

## Step 1 — Apify actors to run

Sign in to <https://console.apify.com/> and create runs for these actors. Each one outputs a dataset.

| Actor | What it pulls | Suggested input |
|---|---|---|
| `apify/instagram-hashtag-scraper` | Posts under hashtags | `["theblend", "sffitfest", "ottyachtparty", "ottfitfest", "ottwineanddine", "tahoeunscripted"]`, last 30 days, max 200 per tag |
| `apify/instagram-profile-scraper` | Mentions of `@overthetopxp` | Input: `["overthetopxp"]`, with `getMentions: true` |
| `clockworks/tiktok-scraper` | Hashtag posts on TikTok | `searchQueries: ["#theblend", "#sffitfest", "#ottyachtparty"]`, last 30 days |
| `apidojo/twitter-search-scraper` | Twitter / X mentions | `searchTerms: ["OTT yacht", "SF Fit Fest", "Blend Coffee R&B"]` |

Each actor produces a **dataset**. Note each dataset ID from the run details page (looks like
`OUWl2Yw7yQpaTBczX`).

## Step 2 — Connect each dataset in Windsor

In Windsor.ai → Connectors → `apify_dataset`:
1. Click **Add connection**
2. Paste your Apify API token (from <https://console.apify.com/account/integrations>)
3. Paste the dataset ID for one of the actors above
4. Repeat per dataset (each becomes its own "account" in Windsor)

Schedule each Apify actor to re-run daily so the datasets stay current.

## Step 3 — Pull into the dashboard

Once datasets are connected, ask Claude in this session:

> "Pull Apify mentions from Windsor (connector `apify_dataset`) and add a Mentions tab to the
> dashboard."

Claude will:
1. Call `get_fields` on `apify_dataset` to inspect the schema (varies per actor)
2. Pull rows, save to `data/mentions_apify.json`
3. Map each mention to an event via caption/text keyword matching (same series rules)
4. Add a "Earned media — mentions" column to the unified marketing table
5. Add a Mentions detail page per upcoming event

## What the dashboard will then show

For each upcoming event, in addition to today's Paid FB / IG organic / TikTok organic columns:

| Source | Metric |
|---|---|
| Earned IG mentions | Count of unique posts mentioning the event, total reach |
| Earned TikTok hashtag posts | Count + total views |
| Twitter mentions | Count + reach |
| Top earned posts (by reach) | Same drill-down as your own organic posts |

This closes the loop: paid + owned + earned media all rolled up against ticket pacing.
