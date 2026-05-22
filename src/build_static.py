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
from src.marketing import (
    attribute_ads_to_events, current_pace, event_marketing_summary, event_marketing_table,
    load_ads, recommended_daily_spend,
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


def build_marketing_block(event_row: pd.Series, marketing_table: pd.DataFrame,
                          attributed_ads: pd.DataFrame, target: int, days: int) -> str:
    """For an upcoming event with a target, show ad-spend status + recommendation."""
    if marketing_table.empty or attributed_ads.empty:
        return ""
    ff_mkt = marketing_table[marketing_table["instance_key"] == event_row["instance_key"]]
    if ff_mkt.empty:
        return ""
    r = ff_mkt.iloc[0]
    total_imp = int(r["impressions"])
    total_spend = float(r["spend"])
    if total_imp == 0:
        return "<p class='caption'>No paid ad campaigns yet attributed to this event.</p>"

    pace = current_pace(attributed_ads, event_row["instance_key"], days=7)
    rec = recommended_daily_spend(event_row, marketing_table, target, days)

    # Build a comparison: current pace vs recommended pace
    daily_imp_now = pace.get("daily_impressions", 0)
    daily_spend_now = pace.get("daily_spend", 0)
    daily_imp_need = rec.get("daily_impressions_needed", 0)
    daily_spend_need = rec.get("daily_spend_needed", 0) or 0
    multiplier = (daily_imp_need / daily_imp_now) if daily_imp_now > 0 else None

    verdict = ""
    if multiplier is None:
        verdict = "⚠️ No current ad activity — need to start spending now."
    elif multiplier <= 1.1:
        verdict = "✅ Current marketing pace matches what's needed to hit target."
    elif multiplier <= 2:
        verdict = f"⚠️ Need to roughly DOUBLE current marketing pace ({multiplier:.1f}× current)."
    else:
        verdict = f"🔴 Need to {multiplier:.1f}× current marketing pace to hit target."

    return f"""
    <h4>Marketing spend status (FB ads)</h4>
    <table class="scenarios">
      <thead><tr><th>Metric</th><th>So far</th><th>Recent pace (last 7d)</th><th>Needed pace</th></tr></thead>
      <tbody>
        <tr><td>Impressions</td><td class='num'>{total_imp:,}</td><td class='num'>{daily_imp_now:,.0f}/day</td><td class='num'><b>{daily_imp_need:,}/day</b></td></tr>
        <tr><td>Spend</td><td class='num'>${total_spend:,.0f}</td><td class='num'>${daily_spend_now:,.0f}/day</td><td class='num'><b>${daily_spend_need:,.0f}/day</b></td></tr>
        <tr><td>Tickets sold</td><td class='num'>{int(r['tickets_sold']):,}</td><td class='num'>—</td><td class='num'>target {target:,}</td></tr>
        <tr><td>CPA so far</td><td class='num'>${r['cpa']:.2f}</td><td class='num'>—</td><td class='num'>historical median ${rec.get('median_cpa', 0):.2f}</td></tr>
        <tr><td>Impressions/ticket</td><td class='num'>{r['impressions_per_ticket']:.0f}</td><td class='num'>—</td><td class='num'>historical median {rec.get('median_impressions_per_ticket', 0):.0f}</td></tr>
      </tbody>
    </table>
    <p><b>{verdict}</b></p>
    <p class="caption">Recommended pace is anchored to past <b>same-series</b> events ({rec.get('past_events_used', 0)} comparable used). Total needed to close the gap: <b>{rec.get('total_impressions_needed', 0):,} impressions</b> at <b>${rec.get('total_spend_needed', 0):,.0f}</b> over {days} days.</p>
    """


def build_marketing_efficiency_table(marketing_table: pd.DataFrame) -> str:
    """Portfolio-level marketing efficiency table (past + upcoming events with ad data)."""
    if marketing_table.empty:
        return ""
    rows = marketing_table[marketing_table["impressions"] > 0].copy()
    if rows.empty:
        return "<p class='empty'>No ad-attributed events yet.</p>"
    rows = rows.sort_values("event_instance_date", ascending=False)
    body = ""
    for _, r in rows.iterrows():
        d = pd.to_datetime(r["event_instance_date"]).strftime("%b %d, %Y")
        roas = f"{r['roas']:.1f}×" if pd.notna(r['roas']) else "—"
        body += f"""
        <tr>
          <td class='ev-name'>{r['event_name']}</td>
          <td>{d}</td>
          <td class='num'>{int(r['impressions']):,}</td>
          <td class='num'>${r['spend']:,.0f}</td>
          <td class='num'>{int(r['tickets_sold']):,}</td>
          <td class='num'>{r['impressions_per_ticket']:.0f}</td>
          <td class='num'>${r['cpa']:.2f}</td>
          <td class='num'><b>{roas}</b></td>
        </tr>"""
    return f"""
    <table>
      <thead><tr>
        <th>Event</th><th>Date</th><th>Impressions</th><th>Spend</th>
        <th>Tickets</th><th>Imp / ticket</th><th>CPA</th><th>ROAS</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def build_event_curve(tickets: pd.DataFrame, row: pd.Series) -> str:
    """Per-event sales-pace chart. Renders each individual past comparable as its own trace
    so the user can click legend entries to toggle them on/off."""
    curve = sales_curve(tickets, row["instance_key"])
    if curve.empty:
        return ""

    fig = go.Figure()

    # 1) This event — always visible, top of legend
    fig.add_trace(go.Scatter(
        x=curve["days_before_event"], y=curve["tickets_cum"],
        mode="lines+markers", name=f"<b>This event ({row['event_name']})</b>",
        line=dict(width=3, color="#6366f1"),
    ))

    # 2) Average of comparables (kept for quick reference)
    comp_avg = comparable_curve(tickets, row)
    if not comp_avg.empty and pd.notna(row["capacity"]):
        comp_avg = comp_avg.copy()
        comp_avg["expected_tickets"] = comp_avg["tickets_pct_of_final"] / 100 * row["capacity"]
        fig.add_trace(go.Scatter(
            x=comp_avg["days_before_event"], y=comp_avg["expected_tickets"],
            mode="lines", name="Avg of comparable runs",
            line=dict(dash="dash", color="#0f172a", width=2),
        ))

    # 3) Each individual comparable as its own trace
    fc = forecast_final_tickets(tickets, row)
    comparables = fc.get("comparables") or []

    # Color palette: distinctive for same-series, muted gray-tones for the rest
    SAME_SERIES_COLORS = ["#dc2626", "#ea580c", "#d97706", "#16a34a"]
    OTHER_COLORS = ["#64748b", "#94a3b8", "#475569", "#334155", "#7c3aed", "#0891b2",
                    "#be185d", "#a16207", "#15803d", "#1d4ed8"]
    same_idx = 0
    other_idx = 0

    for c in comparables:
        # Need to look up the instance_key for this comparable from the tickets DF
        match = tickets[
            (tickets["event_name"] == c["name"]) &
            (tickets["event_instance_date"] == c["date"])
        ]
        if match.empty:
            continue
        ikey = match["instance_key"].iloc[0]
        past_curve = sales_curve(tickets, ikey)
        if past_curve.empty:
            continue

        if c["same_series"]:
            color = SAME_SERIES_COLORS[same_idx % len(SAME_SERIES_COLORS)]
            same_idx += 1
            # Same-series events visible by default — they're the most relevant
            visibility = True
        else:
            color = OTHER_COLORS[other_idx % len(OTHER_COLORS)]
            other_idx += 1
            visibility = "legendonly"  # hidden until user clicks to show

        date_str = c["date"].strftime("%b %Y")
        star = "⭐ " if c["same_series"] else ""
        label = f"{star}{c['name']} · {date_str} · final {c['final']}"
        fig.add_trace(go.Scatter(
            x=past_curve["days_before_event"], y=past_curve["tickets_cum"],
            mode="lines", name=label,
            line=dict(color=color, width=1.5),
            visible=visibility,
        ))

    fig.update_xaxes(autorange="reversed", title="Days before event →")
    fig.update_yaxes(title="Cumulative tickets sold")
    fig.update_layout(
        height=420, hovermode="x unified",
        title=f"{row['event_name']} — sales pace · click legend items to toggle",
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02,
                    font=dict(size=11), bgcolor="rgba(255,255,255,0.9)"),
        margin=dict(l=10, r=10, t=36, b=10),
        paper_bgcolor="white", plot_bgcolor="#f8fafc",
        font=dict(family="-apple-system, Segoe UI, Roboto, sans-serif", size=13, color="#1e293b"),
    )
    return _chart(fig)


def render() -> str:
    tickets = load_tickets()
    capacities = load_capacities()
    waitlist = load_waitlist()
    ads = load_ads()
    attributed_ads = attribute_ads_to_events(ads, tickets) if not ads.empty else pd.DataFrame()
    ad_summary = event_marketing_summary(attributed_ads)
    marketing_table = event_marketing_table(tickets, ad_summary) if not tickets.empty else pd.DataFrame()

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
          {build_marketing_block(r, marketing_table, attributed_ads, target, days)}
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
    marketing_efficiency_html = build_marketing_efficiency_table(marketing_table)

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

  <h2>Marketing → sales efficiency</h2>
  {marketing_efficiency_html}
  <div class="note">
    <b>Imp / ticket</b> = total ad impressions ÷ tickets sold for that event (lower = more efficient creative).
    <b>CPA</b> = ad spend per ticket sold. <b>ROAS</b> = revenue ÷ ad spend.
    Campaigns are attributed to events by name matching ("Fit Fest" campaign → Fit Fest events, etc.) with
    each ad-day going to the next upcoming event in that series. Data source: Windsor.ai → Facebook Ads.
  </div>

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
