"""Render the dashboard as a single static HTML file for GitHub Pages.

Reads the synced SQLite DB + capacities.csv and writes dist/index.html with embedded,
interactive Plotly charts. Only aggregate data is rendered — no attendee PII.

Run: python -m src.build_static
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import (
    IMPRESSION_CONVERSION, comparable_curve, daily_velocity, event_summary,
    forecast_final_tickets, impressions_to_target, load_capacities, load_tickets,
    load_waitlist, pace_flag, sales_curve, show_up_rates,
)

DIST = Path(__file__).resolve().parent.parent / "dist"

PACE_BADGE = {
    "behind": ("BEHIND", "#dc2626"),
    "on_pace": ("ON PACE", "#16a34a"),
    "ahead": ("AHEAD", "#2563eb"),
    "no_baseline": ("NO BASELINE", "#6b7280"),
    "past": ("—", "#6b7280"),
}

CHART_LAYOUT = dict(
    margin=dict(l=10, r=10, t=36, b=10),
    paper_bgcolor="white", plot_bgcolor="#f8fafc",
    font=dict(family="-apple-system, Segoe UI, Roboto, sans-serif", size=13, color="#1e293b"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)


def money(v) -> str:
    return "—" if pd.isna(v) else f"${v:,.0f}"


def _chart(fig: go.Figure, first: bool = False) -> str:
    return pio.to_html(fig, include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False})


def build_velocity_chart(tickets: pd.DataFrame) -> str:
    daily = daily_velocity(tickets, days=60)
    if daily.empty:
        return "<p class='empty'>No recent sales.</p>"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=daily["day"], y=daily["tickets"], name="Tickets", marker_color="#6366f1"))
    fig.add_trace(go.Scatter(x=daily["day"], y=daily["revenue"], name="Revenue ($)",
                             yaxis="y2", mode="lines+markers", line=dict(color="#0891b2")))
    fig.update_layout(
        height=340, hovermode="x unified", title="Daily sales — last 60 days",
        yaxis=dict(title="Tickets"), yaxis2=dict(title="Revenue ($)", overlaying="y", side="right"),
        **CHART_LAYOUT,
    )
    return _chart(fig)


def build_top_events_chart(tickets: pd.DataFrame) -> str:
    by_event = (
        tickets.groupby("event_name", as_index=False)
        .agg(tickets=("id", "count"), revenue=("net_price", "sum"))
        .sort_values("revenue", ascending=False).head(12)
    )
    if by_event.empty:
        return "<p class='empty'>No data.</p>"
    fig = go.Figure(go.Bar(
        x=by_event["revenue"], y=by_event["event_name"], orientation="h",
        marker_color="#6366f1", text=by_event["tickets"].apply(lambda t: f"{t} tix"),
        textposition="auto",
    ))
    fig.update_layout(height=400, title="Top event series by revenue",
                      yaxis=dict(autorange="reversed"), xaxis=dict(title="Revenue ($)"),
                      **CHART_LAYOUT)
    return _chart(fig)


def build_event_curve(tickets: pd.DataFrame, row: pd.Series) -> str:
    curve = sales_curve(tickets, row["instance_key"])
    if curve.empty:
        return ""
    comp = comparable_curve(tickets, row)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=curve["days_before_event"], y=curve["tickets_cum"],
        mode="lines+markers", name="This event", line=dict(width=3, color="#6366f1"),
    ))
    if not comp.empty and pd.notna(row["capacity"]):
        comp = comp.copy()
        comp["expected_tickets"] = comp["tickets_pct_of_final"] / 100 * row["capacity"]
        fig.add_trace(go.Scatter(
            x=comp["days_before_event"], y=comp["expected_tickets"],
            mode="lines", name="Avg of past comparable runs",
            line=dict(dash="dash", color="#94a3b8"),
        ))
    fig.update_xaxes(autorange="reversed", title="Days before event →")
    fig.update_yaxes(title="Cumulative tickets sold")
    fig.update_layout(height=320, hovermode="x unified",
                      title=f"{row['event_name']} — sales pace", **CHART_LAYOUT)
    return _chart(fig)


def render() -> str:
    tickets = load_tickets()
    capacities = load_capacities()
    waitlist = load_waitlist()

    updated = pd.Timestamp.now(tz="US/Pacific").strftime("%B %d, %Y at %-I:%M %p PT")

    if tickets.empty:
        return f"<html><body><h1>OTT Ticketing Dashboard</h1><p>No data yet. Updated {updated}.</p></body></html>"

    summary = event_summary(tickets, capacities, waitlist)
    upcoming = summary[summary["status"] != "past"].copy()
    past = summary[summary["status"] == "past"].copy()
    rates = show_up_rates(tickets)

    # Pace flags for upcoming
    flags = {}
    for _, r in upcoming.iterrows():
        flags[r["instance_key"]] = pace_flag(r, comparable_curve(tickets, r))

    # ---- Summary cards ----
    behind = sum(1 for k, (f, _) in flags.items() if f == "behind")
    cards = [
        ("Upcoming events", f"{len(upcoming)}"),
        ("Tickets sold (upcoming)", f"{int(upcoming['tickets_sold'].sum()):,}"),
        ("Expected attendance", f"{int(upcoming['expected_attendance'].sum()):,}"),
        ("Behind pace", f"{behind}"),
        ("Lifetime revenue", money(tickets["net_price"].sum())),
        ("Lifetime tickets", f"{len(tickets):,}"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='card-label'>{label}</div>"
        f"<div class='card-value'>{value}</div></div>"
        for label, value in cards
    )

    # ---- Upcoming events table ----
    rows_html = ""
    for _, r in upcoming.sort_values("event_instance_date").iterrows():
        flag, reason = flags[r["instance_key"]]
        badge_text, badge_color = PACE_BADGE.get(flag, ("—", "#6b7280"))
        cap = int(r["capacity"]) if pd.notna(r["capacity"]) else "—"
        date_str = pd.to_datetime(r["event_instance_date"]).strftime("%b %d, %Y")
        if pd.notna(r["forecast_final"]):
            fc = f"<b>{int(r['forecast_final'])}</b><br><span class='range'>{int(r['forecast_low'])}–{int(r['forecast_high'])}</span>"
        else:
            fc = "—"
        target = f"{int(r['target_tickets'])}" if pd.notna(r["target_tickets"]) else "—"
        rows_html += f"""
        <tr>
          <td class='ev-name'>{r['event_name']}</td>
          <td>{date_str}</td>
          <td class='num'>{int(r['days_until_event'])}</td>
          <td class='num'>{int(r['paid_tickets'])} / {int(r['free_tickets'])}</td>
          <td class='num'><b>{int(r['tickets_sold'])}</b></td>
          <td class='num forecast'>{fc}</td>
          <td class='num'>{target}</td>
          <td class='num exp'>{int(r['expected_attendance'])}</td>
          <td class='num'>{cap}</td>
          <td class='num'>{money(r['net_revenue'])}</td>
          <td class='num'>{int(r['waitlist_count'])}</td>
          <td><span class='badge' style='background:{badge_color}'>{badge_text}</span></td>
          <td class='why'>{reason}</td>
        </tr>"""

    # ---- Target trackers ----
    target_blocks = []
    for _, r in upcoming.iterrows():
        if pd.isna(r["target_tickets"]):
            continue
        target = int(r["target_tickets"])
        sold = int(r["tickets_sold"])
        days = int(r["days_until_event"])
        gap = max(0, target - sold)
        pct_to_target = sold / target * 100
        forecast_ok = pd.notna(r["forecast_final"]) and r["forecast_final"] >= target

        scenarios = []
        for label, key in [
            ("Email blast to community (open list)", "email_to_list"),
            ("Organic IG/TikTok (warm followers)", "organic_social"),
            ("Paid social retargeting", "warm_paid_social"),
            ("Blended typical mix", "blended_typical"),
            ("Cold paid social ads", "cold_paid_social"),
        ]:
            rate = IMPRESSION_CONVERSION[key]
            r_calc = impressions_to_target(sold, target, rate)
            scenarios.append((label, rate, r_calc["impressions_needed"]))

        per_day = (gap / days) if days > 0 else gap
        forecast_text = (
            f"Forecast pace lands at ~{int(r['forecast_final'])} ({int(r['forecast_low'])}–{int(r['forecast_high'])})"
            if pd.notna(r["forecast_final"]) else "no forecast yet (need comparable past events)"
        )
        verdict = ("✅ on track to hit target" if forecast_ok
                   else "⚠️ off target — extra marketing push needed")
        scen_rows = "".join(
            f"<tr><td>{label}</td><td class='num'>{rate*100:.2f}%</td><td class='num'><b>{imp:,}</b></td></tr>"
            for label, rate, imp in scenarios
        )
        # Build the comparables table that powers this forecast
        fc_detail = forecast_final_tickets(tickets, r)
        comp_html = ""
        if fc_detail.get("comparables"):
            comp_rows = ""
            for c in fc_detail["comparables"]:
                star = " ⭐" if c["same_series"] else ""
                comp_rows += (
                    f"<tr><td>{c['name']}{star}</td>"
                    f"<td>{c['date'].strftime('%b %d, %Y')}</td>"
                    f"<td class='num'>${int(c['mode_price'])}</td>"
                    f"<td class='num'>{c['final']:,}</td>"
                    f"<td class='num'>{c['cum_at_stage']:,} ({c['pct_at_stage']:.0f}%)</td>"
                    f"<td class='num'><b>{c['remaining_from_stage']:,}</b></td></tr>"
                )
            comp_html = f"""
            <h4>Comparables used in this forecast</h4>
            <table class="scenarios">
              <thead><tr><th>Past event</th><th>Date</th><th>Price</th><th>Final</th><th>Sold by T-{int(days)}d</th><th>Remaining from this stage</th></tr></thead>
              <tbody>{comp_rows}</tbody>
            </table>
            <p class="caption">⭐ = same series as this event (matched by name keywords). Forecast = current sold ({sold:,}) + median remaining from these comparables ({fc_detail.get('median_remaining', 0):,}) = {fc_detail.get('forecast', 0):,}.</p>"""
        target_blocks.append(f"""
        <div class="target-card">
          <h3>{r['event_name']} → target {target:,} tickets</h3>
          <div class="progress-row">
            <div class="progress-bar"><div class="progress-fill" style="width:{min(100, pct_to_target):.1f}%"></div></div>
            <div class="progress-label"><b>{sold:,}</b> / {target:,} ({pct_to_target:.0f}%)</div>
          </div>
          <p>{verdict} · {forecast_text}</p>
          <p><b>{gap:,} more tickets needed in {days} days</b> = {per_day:.0f}/day average</p>
          <h4>Impressions needed by channel</h4>
          <table class="scenarios">
            <thead><tr><th>Channel</th><th>Impression → ticket %</th><th>Impressions needed</th></tr></thead>
            <tbody>{scen_rows}</tbody>
          </table>
          <p class="caption">Conversion rates use 2026 industry benchmarks. Mix channels to hit the goal: e.g. one email + organic posts + paid retargeting is usually most cost-efficient.</p>
          {comp_html}
        </div>""")
    target_trackers_html = (
        "<h2>Target trackers</h2>" + "".join(target_blocks)
    ) if target_blocks else ""

    # ---- Per-event curves ----
    curves_html = ""
    for _, r in upcoming.sort_values("event_instance_date").iterrows():
        c = build_event_curve(tickets, r)
        if c:
            curves_html += f"<div class='chart-box'>{c}</div>"

    # ---- Past events table ----
    past_rows = ""
    for _, r in past.sort_values("event_instance_date", ascending=False).iterrows():
        date_str = pd.to_datetime(r["event_instance_date"]).strftime("%b %d, %Y")
        att_rate = f"{r['attendance_rate_pct']:.0f}%" if pd.notna(r["attendance_rate_pct"]) else "—"
        past_rows += f"""
        <tr>
          <td class='ev-name'>{r['event_name']}</td>
          <td>{date_str}</td>
          <td class='num'>{int(r['paid_tickets'])} / {int(r['free_tickets'])}</td>
          <td class='num'><b>{int(r['tickets_sold'])}</b></td>
          <td class='num'>{int(r['attended_count'])}</td>
          <td class='num'>{att_rate}</td>
          <td class='num'>{money(r['net_revenue'])}</td>
        </tr>"""

    velocity_html = build_velocity_chart(tickets)
    top_html = build_top_events_chart(tickets)

    paid_n, free_n = rates.get("paid_n", 0), rates.get("free_n", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>OTT Ticketing Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #f1f5f9; color: #1e293b; }}
  header {{ background: #0f172a; color: #fff; padding: 28px 40px; }}
  header h1 {{ margin: 0; font-size: 24px; }}
  header .updated {{ color: #94a3b8; font-size: 13px; margin-top: 6px; }}
  main {{ max-width: 1200px; margin: 0 auto; padding: 28px 40px 60px; }}
  h2 {{ font-size: 17px; margin: 36px 0 14px; color: #0f172a;
        border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
  .cards {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; }}
  .card-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
                 color: #64748b; }}
  .card-value {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; font-size: 13px; }}
  th {{ background: #f8fafc; text-align: left; padding: 10px 12px; font-size: 11px;
        text-transform: uppercase; letter-spacing: .03em; color: #64748b; }}
  td {{ padding: 10px 12px; border-top: 1px solid #f1f5f9; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.exp {{ font-weight: 700; color: #6366f1; }}
  td.forecast {{ font-weight: 700; color: #0891b2; }}
  td.forecast .range {{ font-weight: 400; color: #64748b; font-size: 11px; }}
  td.ev-name {{ font-weight: 600; max-width: 220px; }}
  td.why {{ color: #64748b; font-size: 12px; max-width: 240px; }}
  .target-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
                  padding: 18px 22px; margin-bottom: 16px; }}
  .target-card h3 {{ margin: 0 0 12px; font-size: 16px; color: #0f172a; }}
  .target-card h4 {{ margin: 18px 0 8px; font-size: 13px; color: #475569;
                     text-transform: uppercase; letter-spacing: .03em; }}
  .target-card p {{ margin: 6px 0; }}
  .target-card .caption {{ color: #64748b; font-size: 12px; margin-top: 10px; }}
  .progress-row {{ display: flex; align-items: center; gap: 14px; margin: 8px 0 14px; }}
  .progress-bar {{ flex: 1; height: 10px; background: #f1f5f9; border-radius: 999px;
                   overflow: hidden; }}
  .progress-fill {{ height: 100%; background: linear-gradient(90deg, #6366f1, #0891b2); }}
  .progress-label {{ font-variant-numeric: tabular-nums; min-width: 130px; text-align: right; }}
  table.scenarios {{ font-size: 12px; margin-top: 4px; }}
  table.scenarios th {{ font-size: 10px; }}
  .badge {{ color: #fff; padding: 3px 9px; border-radius: 999px; font-size: 11px;
            font-weight: 700; white-space: nowrap; }}
  .chart-box {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
                padding: 12px; margin-bottom: 16px; }}
  .note {{ background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
           padding: 12px 14px; font-size: 13px; color: #92400e; margin: 14px 0; }}
  .empty {{ color: #94a3b8; font-style: italic; }}
  footer {{ max-width: 1200px; margin: 0 auto; padding: 0 40px 40px;
            color: #94a3b8; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>OTT Ticketing Dashboard</h1>
  <div class="updated">Auto-updated {updated} &nbsp;·&nbsp; data from SweatPals</div>
</header>
<main>
  <div class="cards">{cards_html}</div>

  <h2>Pipeline health — upcoming events</h2>
  <table>
    <thead><tr>
      <th>Event</th><th>Date</th><th>Days out</th><th>Paid / Free</th><th>Sold</th>
      <th>Forecast final</th><th>Target</th>
      <th>Will show up</th><th>Capacity</th><th>Revenue</th><th>Waitlist</th>
      <th>Pace</th><th>Why</th>
    </tr></thead>
    <tbody>{rows_html if rows_html else '<tr><td colspan=13 class=empty>No upcoming events on sale.</td></tr>'}</tbody>
  </table>
  <div class="note">
    <b>Forecast final</b> = projected total ticket sales by event day, based on the median pace of past
    paid events at a similar price point that finished with ≥200 tickets. Range shows the 25th–75th
    percentile of those comparables.
    <br><b>Will show up</b> = paid sold × paid show-up rate + free sold × free show-up rate.
    OTT historical rates: <b>paid {rates['paid']*100:.0f}%</b> (n={paid_n:,}),
    <b>free {rates['free']*100:.0f}%</b> (n={free_n:,}).
    <br><b>Pace</b> compares sell-through vs. past instances of the same series at the same days-out.
  </div>
  {target_trackers_html}

  <h2>Sales pace — upcoming events</h2>
  {curves_html if curves_html else "<p class='empty'>No upcoming events with sales yet.</p>"}

  <h2>Sales velocity</h2>
  <div class="chart-box">{velocity_html}</div>

  <h2>Portfolio</h2>
  <div class="chart-box">{top_html}</div>

  <h2>Past events</h2>
  <table>
    <thead><tr>
      <th>Event</th><th>Date</th><th>Paid / Free</th><th>Tickets sold</th>
      <th>Attended</th><th>Show-up rate</th><th>Revenue</th>
    </tr></thead>
    <tbody>{past_rows if past_rows else '<tr><td colspan=7 class=empty>No past events.</td></tr>'}</tbody>
  </table>
</main>
<footer>
  Generated from SweatPals API data. Aggregate metrics only — no personal attendee data is published.
</footer>
</body>
</html>"""


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    html = render()
    (DIST / "index.html").write_text(html, encoding="utf-8")
    # .nojekyll keeps GitHub Pages from running Jekyll on the artifact
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote {DIST / 'index.html'} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
