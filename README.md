# SweatPals Ticketing Dashboard

Local analytics dashboard over the SweatPals public API. Answers:

- Which upcoming events are **behind sales pace** vs. comparable past events?
- What % of capacity has each event sold?
- How fast are tickets moving day-over-day?
- Which events drove the most revenue?

## Setup (one time)

```bash
# 1. Install deps (already done — venv lives in .venv/)
.venv/bin/pip install -r requirements.txt

# 2. Drop your API key + host into .env
cp .env.example .env
# then edit .env:
#   SWEATPALS_API_KEY=...
#   SWEATPALS_HOST=https://ilove.sweatpals.com
```

The SweatPals public API lives at `https://ilove.sweatpals.com` and authenticates via the
`x-api-key` header. (The Postman collection uses a `{{host}}` variable but doesn't tell you what
to put in it — that's what threw us at first.)

How to get the API key: SweatPals web → Integrations panel → Zapier → **Add API Key** → **Create New API Key**.

## Daily use

```bash
# Pull latest data into local SQLite (./data/sweatpals.db)
.venv/bin/python -m src.sync

# Open the dashboard at http://localhost:8501
.venv/bin/streamlit run app.py
```

First sync paginates through the entire history. After that the upserts are idempotent — re-running just adds new orders/tickets.

## Capacity setup (unlocks pace tracking)

The SweatPals API does **not** expose event capacity, so we maintain it manually.

1. Run a sync first so events appear in the DB.
2. In the dashboard, open the **Capacity config** tab → click "Generate capacities.csv template".
3. Edit `config/capacities.csv` and fill in the `capacity` column for each event.
4. Reload the dashboard. Sell-through % and **on pace / behind / ahead** flags now light up.

## How "pace" is computed

For each upcoming event, the dashboard:

1. Finds up to 5 past events with the **same event name**.
2. Builds an average sales curve from those: `% of final tickets sold` plotted against `days before event`.
3. Compares the upcoming event's current sell-through vs. that historical curve at the same days-out.
4. Flags it **🔴 behind** (>10pp under historical pace), **🟢 on pace** (±10pp), or **🔵 ahead** (>10pp over).

If an event has no past instances with the same name, you'll see "no baseline" — that's expected for first-time events.

## Project layout

```
.
├── app.py                       # Streamlit dashboard entrypoint
├── src/
│   ├── sweatpals_client.py      # API client (auth probing, pagination, retries)
│   ├── store.py                 # SQLite schema + idempotent upserts
│   ├── sync.py                  # `python -m src.sync` to refresh local data
│   └── analytics.py             # Pure pandas analytics (event_summary, sales_curve, pace_flag)
├── config/
│   └── capacities.csv           # You edit this. event_id, event_name, capacity.
├── data/
│   └── sweatpals.db             # Local SQLite (gitignored)
├── requirements.txt
└── .env                         # SWEATPALS_API_KEY + SWEATPALS_HOST (gitignored)
```

## Troubleshooting

- **Auth fails on first sync**: The client probes `api-key`, `x-api-key`, `Api-Key`, `Authorization` headers automatically. (Confirmed: SweatPals uses `x-api-key`.) If all 4 fail, the API key or host is wrong. Re-check both.
- **`/orders` endpoint returns 404**: That endpoint isn't enabled on every SweatPals account. The sync gracefully skips it and the dashboard derives all metrics from `/new-tickets` instead — same data, slightly different shape (we lose refund visibility but keep revenue, capacity, attendance).
- **Empty dashboard**: Run `.venv/bin/python -m src.sync` first.
- **`event_start_at` looks wrong / dollar amounts off by 100×**: `amount` may come back in cents — check the first few rows with `sqlite3 data/sweatpals.db "SELECT amount, base_amount FROM orders LIMIT 5;"` and adjust `analytics.py:load_orders()` to divide by 100 if needed.
