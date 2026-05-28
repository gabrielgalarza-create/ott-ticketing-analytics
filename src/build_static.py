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
from src.social import (
    attribute_posts_to_events, load_all_posts, top_posts_for_event, unified_marketing_summary,
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


def build_unified_channel_table(unified_summary: pd.DataFrame) -> str:
    """Per-event table of impressions split into Paid / Owned organic / Earned organic."""
    if unified_summary.empty:
        return "<p class='empty'>No marketing data.</p>"
    rows = unified_summary[unified_summary["total_impressions"] > 0].copy()
    if rows.empty:
        return "<p class='empty'>No attributed impressions yet.</p>"
    rows = rows.sort_values("event_instance_date", ascending=False)

    body = ""
    for _, r in rows.iterrows():
        d = pd.to_datetime(r["event_instance_date"]).strftime("%b %d, %Y")
        paid_imp = int(r.get("paid_impressions", 0) or 0)
        paid_spend = float(r.get("paid_spend", 0) or 0)
        owned_v = int(r.get("organic_owned_views", 0) or 0)
        owned_p = int(r.get("organic_owned_posts", 0) or 0)
        collab_v = int(r.get("organic_collab_views", 0) or 0)
        collab_p = int(r.get("organic_collab_posts", 0) or 0)
        earned_v = int(r.get("organic_earned_views", 0) or 0)
        earned_p = int(r.get("organic_earned_posts", 0) or 0)
        total = int(r["total_impressions"])
        ipt = r.get("impressions_per_ticket", 0) or 0
        flag = ""
        if paid_imp > 0 and (owned_v + collab_v) == 0:
            flag = "<span class='gap-flag' title='Paid ads running but no owned/collab organic posts'>⚠️</span>"
        body += f"""
        <tr>
          <td class='ev-name'>{r['event_name']}</td>
          <td>{d}</td>
          <td class='num'>{int(r['tickets_sold']):,}</td>
          <td class='num'>{paid_imp:,}<br><span class='subnum'>${paid_spend:,.0f}</span></td>
          <td class='num'>{owned_v:,}<br><span class='subnum'>{owned_p} post{'s' if owned_p != 1 else ''}</span></td>
          <td class='num'><span class='collab-cell'>{collab_v:,}</span><br><span class='subnum'>{collab_p} post{'s' if collab_p != 1 else ''}</span></td>
          <td class='num'><span class='earned-cell'>{earned_v:,}</span><br><span class='subnum'>{earned_p} post{'s' if earned_p != 1 else ''}</span></td>
          <td class='num'><b>{total:,}</b>{flag}</td>
          <td class='num'>{ipt:.0f}</td>
        </tr>"""
    return f"""
    <table>
      <thead><tr>
        <th>Event</th><th>Date</th><th>Tickets</th>
        <th>Paid FB ads</th>
        <th>Owned<br><span class='subhead'>(OTT-created)</span></th>
        <th>Earned · amplified<br><span class='subhead'>(community post, OTT collab-boosted)</span></th>
        <th>Earned · organic<br><span class='subhead'>(community, not boosted)</span></th>
        <th>Total impressions</th><th>Imp / ticket</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def build_recommendations_banner(summary: pd.DataFrame, marketing_table: pd.DataFrame,
                                 attributed_ads: pd.DataFrame, attributed_posts: pd.DataFrame,
                                 tickets: pd.DataFrame) -> str:
    """Top-of-dashboard 'what lever to pull' recommendation for each behind-pace upcoming event,
    plus the proven content formats worth replicating."""
    from src.marketing import current_organic_pace, current_pace, recommended_daily_spend, _event_series

    upcoming = summary[summary["status"] != "past"].copy()
    if upcoming.empty:
        return ""

    rec_cards = []
    for _, r in upcoming.sort_values("event_instance_date").iterrows():
        ik = str(r["instance_key"])
        days = int(r["days_until_event"]) if pd.notna(r["days_until_event"]) else 0
        sold = int(r["tickets_sold"])
        target = (int(r["target_tickets"]) if pd.notna(r["target_tickets"])
                  else (int(r["capacity"]) if pd.notna(r["capacity"]) else None))
        if not target or days <= 0:
            continue
        forecast = r["forecast_final"] if pd.notna(r["forecast_final"]) else None
        on_track = forecast is not None and forecast >= target
        cap = int(r["capacity"]) if pd.notna(r["capacity"]) else None
        has_explicit_target = pd.notna(r["target_tickets"])

        # Pace vs comparable past events — SAME signal as the Pipeline-health table
        flag, pace_reason = pace_flag(r, comparable_curve(tickets, r))

        rec = recommended_daily_spend(r, marketing_table, target, days) or {}
        pace = current_pace(attributed_ads, ik, days=7)
        cur_imp = pace.get("daily_impressions", 0) or 0
        cur_spend = pace.get("daily_spend", 0) or 0
        need_imp = rec.get("daily_impressions_needed", 0) or 0
        need_spend = rec.get("daily_spend_needed", 0) or 0
        mult = (need_imp / cur_imp) if cur_imp > 0 else None
        fc_txt = f"forecast ~{int(forecast):,}" if forecast is not None else "no forecast yet (first run)"

        # The "how to chase the target" clause — only when there's a real gap to close
        def scale_clause():
            if cur_imp == 0:
                return f"To chase the {target:,} {'target' if has_explicit_target else 'cap'}, start paid at <b>~${need_spend:,.0f}/day</b> (~{need_imp:,} impressions/day)."
            if mult is not None and mult >= 1.2:
                return f"To chase the {target:,} {'target' if has_explicit_target else 'cap'}, scale paid to <b>~${need_spend:,.0f}/day</b> (now ~${cur_spend:,.0f}/day, ~{mult:.1f}× current)."
            return f"Current spend (~${cur_spend:,.0f}/day) is about right for the pace — keep posting to push toward {target:,}."

        target_word = "target" if has_explicit_target else "venue cap"
        if flag == "behind":
            color, verdict = "#dc2626", "🔴 Behind pace — action needed"
            action = f"Selling slower than past events ({pace_reason}). {fc_txt}. {scale_clause()}"
        elif flag == "ahead":
            if on_track:
                color, verdict = "#16a34a", "✅ Ahead of pace · on track to beat goal"
                action = f"Selling faster than past events ({pace_reason}). {fc_txt} — at/above the {target:,} {target_word}. Hold the pace."
            else:
                color, verdict = "#2563eb", f"🔵 Ahead of pace · {target:,} {target_word} is a stretch"
                action = f"Selling faster than past events ({pace_reason}), tracking to ~{int(forecast):,} of {target:,}. {scale_clause()}"
        elif flag == "on_pace":
            if on_track:
                color, verdict = "#16a34a", "🟢 On pace · on track for goal"
                action = f"Tracking with past events. {fc_txt} vs {target:,} {target_word}. Hold steady."
            else:
                color, verdict = "#d97706", f"🟡 On pace · short of {target:,} {target_word}"
                action = f"Tracking with past events but {fc_txt} is under the goal. {scale_clause()}"
        else:  # no_baseline — first run of this event, judge on forecast vs target only
            if forecast is None:
                color, verdict = "#6b7280", "⚪ Too early to call"
                action = f"First run of this event — not enough history to gauge pace yet. {sold:,}/{target:,} sold."
            elif on_track:
                color, verdict = "#16a34a", "✅ On track for goal"
                action = f"No comparable history, but {fc_txt} ≥ {target:,} {target_word}. Keep going."
            else:
                color, verdict = "#d97706", f"🟡 Forecast under {target:,} {target_word}"
                action = f"No comparable history to judge pace. {fc_txt}. {scale_clause()}"

        # Channel breakdown: current paid vs organic reach + where to dial up
        org = current_organic_pace(attributed_posts, ik, days=14)
        org_daily = org.get("daily_views", 0) or 0
        org_posts_wk = org.get("posts_per_week", 0) or 0
        paid_part = (f"<b>Paid:</b> ~{cur_imp:,.0f} impressions/day (${cur_spend:,.0f}/day)"
                     if cur_imp else "<b>Paid:</b> none running")
        org_part = (f"<b>Organic:</b> ~{org_daily:,.0f} views/day, {org_posts_wk:g} posts/wk"
                    if org_daily else "<b>Organic:</b> no recent posts attributed")
        # Dial-up guidance per channel
        dial = []
        if flag == "behind" or (not on_track and flag in ("on_pace", "no_baseline", "ahead")):
            if cur_imp == 0:
                dial.append("turn paid <b>on</b>")
            elif mult is not None and mult >= 1.2:
                dial.append(f"raise <b>paid</b> ~{mult:.1f}× → ${need_spend:,.0f}/day")
            if org_posts_wk < 3:
                dial.append("post <b>organic more often</b> (aim ~3–5×/wk)")
        dial_txt = (" &nbsp;→ Dial up: " + ", ".join(dial)) if dial else ""
        channel_html = f"<div class='rec-channels'>{paid_part} &nbsp;·&nbsp; {org_part}{dial_txt}</div>"

        # Week-of surge line
        surge_share = r.get("surge_share")
        surge_tix = r.get("surge_tickets")
        surge_html = ""
        if pd.notna(surge_share) and surge_share and surge_share > 0 and forecast is not None:
            surge_html = (f"<div class='rec-surge'>📈 Forecast includes the week-of surge: historically "
                          f"~{surge_share*100:.0f}% of sales land in the final 7 days "
                          f"(~{int(surge_tix):,} of the forecast) — peak spend + posting that week.</div>")

        date_str = pd.to_datetime(r["event_instance_date"]).strftime("%b %-d")
        cap_txt = f" · {cap} cap" if cap and has_explicit_target else ""
        rec_cards.append(f"""
        <div class="rec-card" style="border-left:4px solid {color}">
          <div class="rec-head">{r['event_name']} <span class="subnum">· {date_str} · {days}d out · {sold:,}/{target:,} {target_word}{cap_txt}</span></div>
          <div class="rec-verdict" style="color:{color}">{verdict}</div>
          <div class="rec-action">{action}</div>
          {channel_html}
          {surge_html}
        </div>""")

    # Proven content formats: top organic posts by views across all events
    formats_html = ""
    if not attributed_posts.empty:
        top = attributed_posts.sort_values("views", ascending=False).head(4)
        rows = ""
        for _, p in top.iterrows():
            ch = "IG" if p["channel"] == "instagram_organic" else "TikTok"
            cap = (p["caption"][:90] + "…") if len(p["caption"]) > 90 else p["caption"]
            cap = cap.replace("\n", " ")
            link = f"<a href='{p['url']}' target='_blank' rel='noopener'>↗</a>" if p.get("url") else ""
            rows += f"<li><b>{int(p['views']):,} views</b> · {ch} · @{p.get('owner','')} — {cap} {link}</li>"
        formats_html = f"""
        <div class="rec-card" style="border-left:4px solid #6366f1">
          <div class="rec-head">📈 Proven content formats (your highest-reach posts — make more like these)</div>
          <ul class="rec-list">{rows}</ul>
        </div>"""

    if not rec_cards and not formats_html:
        return ""
    return f"""
    <h2 style="margin-top:0">🎯 What to dial up now</h2>
    <div class="rec-grid">{''.join(rec_cards)}{formats_html}</div>"""


def build_review_queue(attributed_posts: pd.DataFrame, tickets: pd.DataFrame) -> str:
    """Show ambiguous posts that need manual attribution review."""
    if attributed_posts.empty or "is_ambiguous" not in attributed_posts.columns:
        return ""
    queue = attributed_posts[attributed_posts["is_ambiguous"] == True].copy()
    if queue.empty:
        return """<div class="note">
        ✅ Every attributed post passed the ambiguity check (recap-vs-promo and date-mention rules).
        Posts you want to reroute manually can be added to <code>config/post_overrides.csv</code>.
        </div>"""

    # Build event-key → "Event Name (Date)" lookup. All blend/fit-fest/etc events become
    # selectable options so you're never limited to just the suggested alternatives.
    import json as _json
    ev_meta = tickets.groupby("instance_key", as_index=False).agg(
        event_name=("event_name", "last"),
        event_instance_date=("event_instance_date", "last"),
    ).sort_values("event_instance_date")
    ev_meta["instance_key"] = ev_meta["instance_key"].astype(str)
    ev_label = {row["instance_key"]: f"{row['event_name']} ({pd.to_datetime(row['event_instance_date']).strftime('%b %-d, %Y')})"
                for _, row in ev_meta.iterrows()}
    # Options list for the dropdowns (most recent first)
    options_js = [{"key": k, "label": v} for k, v in
                  sorted(ev_label.items(), key=lambda kv: kv[1])]

    rows_html = ""
    for _, p in queue.sort_values("views", ascending=False).iterrows():
        ch_label = "IG" if p["channel"] == "instagram_organic" else "TikTok"
        d = pd.to_datetime(p["date"]).strftime("%b %-d, %Y")
        cap = (p["caption"][:200] + "…") if len(p["caption"]) > 200 else p["caption"]
        cap = cap.replace("\n", " ")
        url = p["url"] or ""
        # Fall back to a canonical IG/TikTok URL from the post_id if no permalink was captured
        if not url:
            pid = str(p["post_id"])
            if p["channel"] == "tiktok_organic":
                url = f"https://www.tiktok.com/@overthetopxp/video/{pid}"
        post_link = (f"<a class='post-open' href='{url}' target='_blank' rel='noopener'>Open post ↗</a>"
                     if url else "<span class='subnum'>no link</span>")
        current_key = str(p["instance_key"])
        current = ev_label.get(current_key, p["event_name"])
        alts = [k.strip() for k in (p.get("alt_instance_keys") or "").split(",") if k.strip()]

        # Build the <select>: current first (pre-selected), then suggested alts, then divider, then all events, then Ignore
        opt_html = f"<option value='{current_key}' selected>✓ Keep: {ev_label.get(current_key, current)}</option>"
        for k in alts:
            if k in ev_label:
                opt_html += f"<option value='{k}'>⭐ Suggested: {ev_label[k]}</option>"
        opt_html += "<option disabled>──────────</option>"
        for o in options_js:
            if o["key"] != current_key and o["key"] not in alts:
                opt_html += f"<option value='{o['key']}'>{o['label']}</option>"
        opt_html += "<option disabled>──────────</option>"
        opt_html += "<option value='ignore'>🚫 Ignore — not event-specific</option>"

        rows_html += f"""
        <tr data-postid="{p['post_id']}" data-current="{current_key}">
          <td><span class='ch-badge'>{ch_label}</span><br>{post_link}</td>
          <td>{d}<br><span class='subnum'>@{p.get('owner', '')}</span></td>
          <td class='num'><b>{int(p['views']):,}</b></td>
          <td class='post-caption'><b>Why flagged:</b> {p['ambiguity_reason']}<br><br>{cap}</td>
          <td><select class='attr-select' data-postid="{p['post_id']}" onchange='ottMarkChanged(this)'>{opt_html}</select></td>
        </tr>"""

    return f"""
    <div class="note">
      <b>{len(queue)} post{'s' if len(queue) != 1 else ''} flagged for review.</b> The auto-router
      chose an event for each, but the caption suggests it could belong to a different one
      (recap language pointing back to a past event, or an explicit date that doesn't match).
      <br><br>
      <b>1.</b> Pick the correct event from each dropdown (leave on "✓ Keep" if it's already right).
      &nbsp; <b>2.</b> Click <b>Copy selections</b>.
      &nbsp; <b>3.</b> Paste them to Claude in chat — I'll apply them and redeploy.
      <br><span class="subnum">(GitHub Pages is a static site, so there's no live "save" button — pasting the
      copied text to Claude, or committing it to <code>config/post_overrides.csv</code>, is how it gets applied.)</span>
    </div>
    <table>
      <thead><tr>
        <th>Ch</th><th>Posted</th><th>Views</th><th>Caption / why flagged</th>
        <th style="min-width:260px">Attribute to →</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div style="margin:16px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button id="ott-gen-btn" class="ott-btn" onclick="ottCopyOverrides()">📋 Copy selections (paste to Claude)</button>
      <button class="ott-btn ott-btn-ghost" onclick="ottDownloadOverrides()">Download CSV instead</button>
      <span id="ott-gen-status" class="subnum"></span>
    </div>
    <textarea id="ott-overrides-out" class="ott-output" placeholder="Make your selections above, then click Copy. The lines to paste to Claude will appear here…" readonly></textarea>
    <script>
      function ottMarkChanged(sel) {{
        const changed = sel.value !== sel.closest('tr').dataset.current;
        sel.style.borderColor = changed ? '#ea580c' : '#e2e8f0';
        sel.style.background = changed ? '#fff7ed' : '#fff';
      }}
      function ottBuildCsv() {{
        const lines = ['post_id,instance_key,note'];
        document.querySelectorAll('.attr-select').forEach(sel => {{
          const tr = sel.closest('tr');
          const cur = tr.dataset.current;
          if (sel.value !== cur) {{
            const label = sel.options[sel.selectedIndex].text.replace(/^[^A-Za-z0-9]+/, '').replace(/,/g, ';');
            lines.push(`${{sel.dataset.postid}},${{sel.value}},reassigned via dashboard: ${{label}}`);
          }}
        }});
        return lines;
      }}
      function ottGenerateOverrides() {{
        const lines = ottBuildCsv();
        const out = document.getElementById('ott-overrides-out');
        const status = document.getElementById('ott-gen-status');
        if (lines.length === 1) {{
          out.value = '';
          status.textContent = 'No changes selected yet — every post is still on "Keep".';
          return;
        }}
        out.value = lines.join('\\n') + '\\n';
        status.textContent = (lines.length - 1) + ' selection(s) ready — paste to Claude or commit to config/post_overrides.csv.';
      }}
      function ottCopyOverrides() {{
        ottGenerateOverrides();
        const out = document.getElementById('ott-overrides-out');
        if (!out.value) return;
        navigator.clipboard.writeText(out.value).then(() => {{
          document.getElementById('ott-gen-status').textContent = 'Copied to clipboard ✓';
        }});
      }}
      function ottDownloadOverrides() {{
        ottGenerateOverrides();
        const out = document.getElementById('ott-overrides-out');
        if (!out.value) return;
        const blob = new Blob([out.value], {{type: 'text/csv'}});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'post_overrides.csv';
        a.click();
      }}
    </script>"""


def build_top_posts_block(attributed_posts: pd.DataFrame, instance_key: str, n: int = 5) -> str:
    """Top performing organic posts for an event — split into Owned and Earned."""
    if attributed_posts.empty:
        return ""
    sub = attributed_posts[attributed_posts["instance_key"] == instance_key].copy()
    if sub.empty:
        return "<p class='caption'>No organic posts attributed to this event yet.</p>"

    def render_table(rows: pd.DataFrame, title: str, empty_msg: str) -> str:
        if rows.empty:
            return f"<h4>{title}</h4><p class='caption'>{empty_msg}</p>"
        body = ""
        for _, p in rows.iterrows():
            d = p["date"].strftime("%b %-d, %Y")
            ch_label = "IG" if p["channel"] == "instagram_organic" else "TikTok"
            caption_short = (p["caption"][:140] + "…") if len(p["caption"]) > 140 else p["caption"]
            url_link = f"<a href='{p['url']}' target='_blank' rel='noopener'>view ↗</a>" if p["url"] else ""
            owner = f"@{p['owner']}" if p.get("owner") else ""
            body += f"""
            <tr>
              <td>{ch_label}</td>
              <td>{d}</td>
              <td>{owner}</td>
              <td class='num'><b>{int(p['views']):,}</b></td>
              <td class='num'>{int(p['likes']):,}</td>
              <td class='post-caption'>{caption_short}</td>
              <td>{url_link}</td>
            </tr>"""
        return f"""
        <h4>{title}</h4>
        <table class="scenarios">
          <thead><tr><th>Ch</th><th>Posted</th><th>Author</th><th>Views</th><th>Likes</th><th>Caption</th><th></th></tr></thead>
          <tbody>{body}</tbody>
        </table>"""

    owned = sub[sub["origin"].isin(["owned", "team"])].sort_values("views", ascending=False).head(n)
    collab = sub[sub["origin"] == "collab"].sort_values("views", ascending=False).head(n)
    earned = sub[sub["origin"] == "earned"].sort_values("views", ascending=False).head(n)
    return (
        render_table(owned, "Top owned organic posts", "No owned posts mentioning this event.")
        + render_table(collab, "Top earned · amplified (community posts OTT collab-boosted)",
                       "No amplified community posts yet — ask creators who posted to collab so it hits your feed too.")
        + render_table(earned, "Top earned · organic (community, not boosted)",
                       "No external accounts have posted about this event yet — a clear gap to seed.")
    )


def build_marketing_pace_chart(tickets: pd.DataFrame, attributed_ads: pd.DataFrame) -> str:
    """Cumulative impressions per event by days-before-event. Each event = its own
    toggleable trace; same-series matches for any upcoming event are visible by default."""
    if attributed_ads.empty or tickets.empty:
        return "<p class='empty'>No ad data to plot.</p>"

    # Series for each upcoming event so we can default-show same-series past events
    from src.marketing import _event_series
    now = pd.Timestamp.now(tz="UTC")
    upcoming_series = set()
    upcoming_keys = set()
    for ik in tickets["instance_key"].unique():
        rows = tickets[tickets["instance_key"] == ik]
        ev_date = rows["event_instance_date"].iloc[0]
        if pd.isna(ev_date) or ev_date < now:
            continue
        upcoming_keys.add(ik)
        s = _event_series(rows["event_name"].iloc[0])
        if s:
            upcoming_series.add(s)

    # Build per-event cumulative impressions
    ads = attributed_ads.copy()
    ads["days_before_event"] = (ads["event_instance_date"] - ads["date"]).dt.days
    # One row per (instance_key, day_before_event) — sum impressions across campaigns
    agg = ads.groupby(["instance_key", "event_name", "event_instance_date", "days_before_event"], as_index=False).agg(
        impressions=("impressions", "sum"), spend=("spend", "sum"),
    )

    # Color palette
    UPCOMING_COLORS = ["#6366f1", "#0891b2", "#7c3aed"]
    SAME_SERIES_COLORS = ["#dc2626", "#ea580c", "#d97706", "#16a34a"]
    OTHER_COLORS = ["#64748b", "#94a3b8", "#475569", "#334155", "#be185d", "#a16207",
                    "#15803d", "#1d4ed8", "#0f766e", "#7e22ce"]
    upcoming_idx = same_idx = other_idx = 0

    fig = go.Figure()
    # Order: upcoming first, then same-series past, then others. Sort within group by event date desc.
    events_meta = agg.groupby(["instance_key", "event_name", "event_instance_date"], as_index=False).first()
    events_meta["is_upcoming"] = events_meta["instance_key"].isin(upcoming_keys)
    events_meta["series"] = events_meta["event_name"].apply(_event_series)
    events_meta["is_same_series"] = (~events_meta["is_upcoming"]) & events_meta["series"].isin(upcoming_series)
    events_meta["sort_key"] = events_meta["is_upcoming"].map({True: 0, False: 1}) * 1_000_000 + \
                              (~events_meta["is_same_series"]).map({True: 1, False: 0}) * 100_000 + \
                              events_meta["event_instance_date"].astype("int64") // -10**14
    events_meta = events_meta.sort_values("sort_key")

    for _, e in events_meta.iterrows():
        sub = agg[agg["instance_key"] == e["instance_key"]].sort_values("days_before_event", ascending=False)
        if sub.empty:
            continue
        sub["impressions_cum"] = sub["impressions"].cumsum()
        date_str = pd.to_datetime(e["event_instance_date"]).strftime("%b %-d, %Y")
        if e["is_upcoming"]:
            color = UPCOMING_COLORS[upcoming_idx % len(UPCOMING_COLORS)]; upcoming_idx += 1
            visibility = True
            label = f"⏳ <b>{e['event_name']} ({date_str})</b>"
        elif e["is_same_series"]:
            color = SAME_SERIES_COLORS[same_idx % len(SAME_SERIES_COLORS)]; same_idx += 1
            visibility = True
            label = f"⭐ {e['event_name']} ({date_str})"
        else:
            color = OTHER_COLORS[other_idx % len(OTHER_COLORS)]; other_idx += 1
            visibility = "legendonly"
            label = f"{e['event_name']} ({date_str})"
        fig.add_trace(go.Scatter(
            x=sub["days_before_event"], y=sub["impressions_cum"],
            mode="lines", name=label, line=dict(color=color, width=2.2),
            visible=visibility,
            hovertemplate="<b>%{fullData.name}</b><br>%{x}d before event<br>%{y:,.0f} impressions<extra></extra>",
        ))

    fig.update_xaxes(autorange="reversed", title="Days before event →")
    fig.update_yaxes(title="Cumulative impressions")
    fig.update_layout(
        height=440, hovermode="x unified",
        title="Cumulative ad impressions vs days-before-event · click legend items to toggle",
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02,
                    font=dict(size=11), bgcolor="rgba(255,255,255,0.9)"),
        margin=dict(l=10, r=10, t=36, b=10),
        paper_bgcolor="white", plot_bgcolor="#f8fafc",
        font=dict(family="-apple-system, Segoe UI, Roboto, sans-serif", size=13, color="#1e293b"),
    )
    return _chart(fig)


def build_campaign_breakdown(attributed_ads: pd.DataFrame,
                             attributed_posts: pd.DataFrame | None = None) -> str:
    """For each event, list its paid ad campaigns/ad sets AND its attributed organic IG/TikTok posts."""
    if attributed_ads.empty and (attributed_posts is None or attributed_posts.empty):
        return "<p class='empty'>No campaign data.</p>"

    has_adset = "adset_name" in attributed_ads.columns if not attributed_ads.empty else False
    if not attributed_ads.empty:
        group_cols = ["instance_key", "event_name", "event_instance_date", "campaign"]
        if has_adset:
            group_cols.append("adset_name")
        by_camp = attributed_ads.groupby(group_cols, as_index=False).agg(
            impressions=("impressions", "sum"), spend=("spend", "sum"), clicks=("clicks", "sum"),
            first_day=("date", "min"), last_day=("date", "max"), ad_days=("date", "nunique"),
        )
        by_camp["share_of_event"] = by_camp.groupby("instance_key")["impressions"].transform(lambda s: s / s.sum() * 100)
        by_camp["ctr"] = (by_camp["clicks"] / by_camp["impressions"] * 100).where(by_camp["impressions"] > 0)
    else:
        by_camp = pd.DataFrame()

    # Event metadata + ordering across BOTH ads and posts
    meta_frames = []
    if not attributed_ads.empty:
        meta_frames.append(attributed_ads[["instance_key", "event_name", "event_instance_date"]])
    if attributed_posts is not None and not attributed_posts.empty:
        meta_frames.append(attributed_posts[["instance_key", "event_name", "event_instance_date"]])
    meta_all = pd.concat(meta_frames, ignore_index=True).dropna(subset=["instance_key"])
    meta_all["event_instance_date"] = pd.to_datetime(meta_all["event_instance_date"], utc=True, errors="coerce")
    meta_all = meta_all.sort_values("event_instance_date", ascending=False).drop_duplicates("instance_key")

    blocks = []
    for _, m in meta_all.iterrows():
        ik = str(m["instance_key"])
        date_str = pd.to_datetime(m["event_instance_date"]).strftime("%b %-d, %Y") if pd.notna(m["event_instance_date"]) else "—"
        ev_camps = by_camp[by_camp["instance_key"].astype(str) == ik].sort_values("impressions", ascending=False) if not by_camp.empty else pd.DataFrame()
        ev_posts = (attributed_posts[attributed_posts["instance_key"].astype(str) == ik].sort_values("views", ascending=False)
                    if attributed_posts is not None and not attributed_posts.empty else pd.DataFrame())

        total_imp = int(ev_camps["impressions"].sum()) if not ev_camps.empty else 0
        total_spend = float(ev_camps["spend"].sum()) if not ev_camps.empty else 0.0
        total_post_views = int(ev_posts["views"].sum()) if not ev_posts.empty else 0

        # --- Ads table ---
        ads_table = ""
        if not ev_camps.empty:
            rows_html = ""
            for _, c in ev_camps.iterrows():
                d_from = c["first_day"].strftime("%b %-d"); d_to = c["last_day"].strftime("%b %-d")
                ctr_txt = f"{c['ctr']:.2f}%" if pd.notna(c["ctr"]) else "—"
                adset_html = f"<br><span class='adset'>↳ {c['adset_name']}</span>" if has_adset and c.get("adset_name") else ""
                rows_html += f"""
              <tr><td><b>{c['campaign']}</b>{adset_html}</td><td>{d_from} – {d_to}</td>
                <td class='num'>{int(c['ad_days'])}</td><td class='num'>{int(c['impressions']):,}</td>
                <td class='num'><b>{c['share_of_event']:.0f}%</b></td><td class='num'>${c['spend']:,.0f}</td>
                <td class='num'>{ctr_txt}</td></tr>"""
            ads_table = f"""
          <h5 class="bd-sub">Paid ad sets ({total_imp:,} impressions · ${total_spend:,.0f})</h5>
          <table class="scenarios">
            <thead><tr><th>Campaign / Ad set</th><th>Active</th><th>Days</th><th>Impressions</th><th>Share</th><th>Spend</th><th>CTR</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>"""

        # --- Organic posts table ---
        posts_table = ""
        if not ev_posts.empty:
            prows = ""
            for _, p in ev_posts.iterrows():
                ch = "IG" if p["channel"] == "instagram_organic" else "TikTok"
                origin = {"owned": "owned", "team": "owned", "collab": "earned·amp", "earned": "earned"}.get(p.get("origin"), "—")
                cap = (p["caption"][:80] + "…") if len(p["caption"]) > 80 else p["caption"]
                cap = cap.replace("\n", " ")
                link = f"<a href='{p['url']}' target='_blank' rel='noopener'>↗</a>" if p.get("url") else ""
                prows += f"""
              <tr><td>{ch}</td><td>@{p.get('owner','')}</td><td>{origin}</td>
                <td class='num'><b>{int(p['views']):,}</b></td><td class='num'>{int(p['likes']):,}</td>
                <td class='post-caption'>{cap} {link}</td></tr>"""
            posts_table = f"""
          <h5 class="bd-sub">Organic posts ({len(ev_posts)} · {total_post_views:,} views)</h5>
          <table class="scenarios">
            <thead><tr><th>Ch</th><th>Author</th><th>Type</th><th>Views</th><th>Likes</th><th>Caption</th></tr></thead>
            <tbody>{prows}</tbody>
          </table>"""

        summary_bits = []
        if total_imp:
            summary_bits.append(f"{total_imp:,} paid impressions · ${total_spend:,.0f}")
        if total_post_views:
            summary_bits.append(f"{len(ev_posts)} organic post{'s' if len(ev_posts)!=1 else ''} · {total_post_views:,} views")
        summary_line = " &nbsp;·&nbsp; ".join(summary_bits) if summary_bits else "no data"
        blocks.append(f"""
        <details class="campaign-breakdown">
          <summary><b>{m['event_name']}</b> &nbsp;·&nbsp; {date_str} &nbsp;·&nbsp; {summary_line}</summary>
          {ads_table}{posts_table}
        </details>""")
    return "".join(blocks)


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

        date_str = c["date"].strftime("%b %-d, %Y")
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
    attributed_ads = attribute_ads_to_events(ads, tickets, capacities, waitlist) if not ads.empty else pd.DataFrame()
    ad_summary = event_marketing_summary(attributed_ads)
    marketing_table = event_marketing_table(tickets, ad_summary) if not tickets.empty else pd.DataFrame()
    # Organic social: IG + TikTok posts attributed to events by caption keyword
    social_posts = load_all_posts()
    attributed_posts = attribute_posts_to_events(social_posts, tickets, capacities, waitlist) if not social_posts.empty else pd.DataFrame()
    unified_summary = unified_marketing_summary(tickets, attributed_ads, attributed_posts)

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

    # ---- Target trackers (filterable by ANY event) ----
    # Order: upcoming first (soonest first), then past (most recent first)
    tracker_order = pd.concat([
        upcoming.sort_values("event_instance_date"),
        past.sort_values("event_instance_date", ascending=False),
    ], ignore_index=True)

    target_blocks = []
    dropdown_opts = []
    # Default to the soonest upcoming event that has an explicit target, else first upcoming
    default_key = None
    with_target = upcoming[upcoming["target_tickets"].notna()].sort_values("event_instance_date")
    if not with_target.empty:
        default_key = str(with_target.iloc[0]["instance_key"])
    elif not upcoming.empty:
        default_key = str(upcoming.sort_values("event_instance_date").iloc[0]["instance_key"])
    elif not tracker_order.empty:
        default_key = str(tracker_order.iloc[0]["instance_key"])

    for _, r in tracker_order.iterrows():
        ik = str(r["instance_key"])
        is_upcoming = r["status"] != "past"
        sold = int(r["tickets_sold"])
        date_str = pd.to_datetime(r["event_instance_date"]).strftime("%b %-d, %Y")
        # Target = explicit target_tickets, else capacity, else None
        if pd.notna(r["target_tickets"]):
            target, target_kind = int(r["target_tickets"]), "target"
        elif pd.notna(r["capacity"]):
            target, target_kind = int(r["capacity"]), "capacity"
        else:
            target, target_kind = None, None

        # Progress bar + headline
        if target:
            pct = min(100, sold / target * 100)
            pct_raw = sold / target * 100
            head = f"{r['event_name']} → {target_kind} {target:,} tickets"
            progress = f"""
          <div class="progress-row">
            <div class="progress-bar"><div class="progress-fill" style="width:{pct:.1f}%"></div></div>
            <div class="progress-label"><b>{sold:,}</b> / {target:,} ({pct_raw:.0f}%)</div>
          </div>"""
        else:
            head = f"{r['event_name']} → {sold:,} tickets sold"
            progress = ""

        # Forecast / gap (upcoming only)
        body = ""
        comp_html = ""
        if is_upcoming:
            days = int(r["days_until_event"])
            forecast_text = (
                f"Forecast pace lands at ~{int(r['forecast_final'])} ({int(r['forecast_low'])}–{int(r['forecast_high'])})"
                if pd.notna(r["forecast_final"]) else "no forecast yet (need comparable past events)"
            )
            surge_share = r.get("surge_share")
            if pd.notna(r["forecast_final"]) and pd.notna(surge_share) and surge_share and surge_share > 0:
                forecast_text += (f" — incl. a typical <b>final-week surge of ~{surge_share*100:.0f}%</b> "
                                  f"(~{int(r['surge_tickets']):,} tickets in the last 7 days)")
            if target:
                forecast_ok = pd.notna(r["forecast_final"]) and r["forecast_final"] >= target
                verdict = ("✅ on track to hit target" if forecast_ok
                           else "⚠️ off target — extra marketing push needed")
                gap = max(0, target - sold)
                per_day = (gap / days) if days > 0 else gap
                body = (f"<p>{verdict} · {forecast_text}</p>"
                        f"<p><b>{gap:,} more tickets needed in {days} days</b> = {per_day:.0f}/day average</p>")
            else:
                body = f"<p>{forecast_text} · {days} days out</p>"
            fc_detail = forecast_final_tickets(tickets, r)
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
              <thead><tr><th>Past event</th><th>Date</th><th>Price</th><th>Final</th><th>Sold by T-{days}d</th><th>Remaining from this stage</th></tr></thead>
              <tbody>{comp_rows}</tbody>
            </table>
            <p class="caption">⭐ = same series. Forecast = current sold ({sold:,}) + median remaining from these comparables ({fc_detail.get('median_remaining', 0):,}) = {fc_detail.get('forecast', 0):,}.</p>"""
        else:
            att = f"{r['attendance_rate_pct']:.0f}% showed up" if pd.notna(r["attendance_rate_pct"]) else ""
            body = f"<p>Final: <b>{sold:,}</b> tickets · {int(r['attended_count'])} attended · {att} · {money(r['net_revenue'])} revenue</p>"

        mkt = build_marketing_block(r, marketing_table, attributed_ads, target or sold, int(r["days_until_event"]) if is_upcoming else 1)
        display = "block" if ik == default_key else "none"
        target_blocks.append(f"""
        <div class="target-card tracker-card" data-key="{ik}" style="display:{display}">
          <h3>{head}</h3>{progress}
          {body}
          {mkt}
          {build_top_posts_block(attributed_posts, ik, n=5)}
          {comp_html}
        </div>""")
        label = f"{'⏳ ' if is_upcoming else ''}{r['event_name']} ({date_str})"
        sel = " selected" if ik == default_key else ""
        dropdown_opts.append(f"<option value='{ik}'{sel}>{label}</option>")

    if target_blocks:
        target_trackers_html = f"""
    <h2>Target tracker</h2>
    <div style="margin-bottom:14px">
      <label class="subnum" for="tracker-select">Show event:&nbsp;</label>
      <select id="tracker-select" class="attr-select" style="max-width:420px;display:inline-block;width:auto"
              onchange="ottShowTracker(this.value)">{''.join(dropdown_opts)}</select>
    </div>
    {''.join(target_blocks)}
    <script>
      function ottShowTracker(key) {{
        document.querySelectorAll('.tracker-card').forEach(c => {{
          c.style.display = (c.dataset.key === key) ? 'block' : 'none';
        }});
      }}
    </script>"""
    else:
        target_trackers_html = ""

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

    top_html = build_top_events_chart(tickets)
    marketing_efficiency_html = build_marketing_efficiency_table(marketing_table)
    marketing_pace_html = build_marketing_pace_chart(tickets, attributed_ads)
    campaign_breakdown_html = build_campaign_breakdown(attributed_ads, attributed_posts)
    review_queue_html = build_review_queue(attributed_posts, tickets)
    unified_channel_html = build_unified_channel_table(unified_summary)
    recommendations_html = build_recommendations_banner(summary, marketing_table, attributed_ads, attributed_posts, tickets)

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
  details.campaign-breakdown {{ background: #fff; border: 1px solid #e2e8f0;
                                border-radius: 8px; padding: 0; margin-bottom: 8px; }}
  details.campaign-breakdown summary {{ cursor: pointer; padding: 12px 16px; font-size: 14px;
                                        list-style: none; user-select: none; }}
  details.campaign-breakdown summary::-webkit-details-marker {{ display: none; }}
  details.campaign-breakdown summary::before {{ content: "▶"; display: inline-block;
                                                 margin-right: 8px; transition: transform .15s;
                                                 color: #6366f1; font-size: 11px; }}
  details.campaign-breakdown[open] summary::before {{ transform: rotate(90deg); }}
  details.campaign-breakdown[open] summary {{ border-bottom: 1px solid #f1f5f9; }}
  details.campaign-breakdown table {{ border: none; border-radius: 0; }}
  details.campaign-breakdown table th,
  details.campaign-breakdown table td {{ font-size: 12px; padding: 8px 16px; }}
  span.adset {{ color: #64748b; font-size: 11px; font-weight: 400; }}
  h5.bd-sub {{ margin: 12px 16px 4px; font-size: 11px; text-transform: uppercase;
               letter-spacing: .04em; color: #64748b; }}
  span.subnum {{ color: #94a3b8; font-size: 11px; font-weight: 400; }}
  span.gap-flag {{ margin-left: 6px; cursor: help; }}
  td.post-caption {{ max-width: 360px; font-size: 12px; color: #475569;
                     overflow: hidden; text-overflow: ellipsis; }}
  .earned-cell {{ color: #16a34a; font-weight: 700; }}
  .collab-cell {{ color: #ea580c; font-weight: 700; }}
  .rec-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .rec-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
               padding: 16px 18px; }}
  .rec-head {{ font-weight: 700; font-size: 14px; color: #0f172a; margin-bottom: 6px; }}
  .rec-verdict {{ font-weight: 700; font-size: 13px; margin-bottom: 6px; }}
  .rec-action {{ font-size: 13px; color: #334155; line-height: 1.5; }}
  .rec-channels {{ font-size: 12px; color: #475569; margin-top: 8px; padding-top: 8px;
                   border-top: 1px dashed #e2e8f0; }}
  .rec-surge {{ font-size: 12px; color: #92400e; background: #fffbeb; border-radius: 6px;
                padding: 7px 9px; margin-top: 8px; }}
  .rec-list {{ margin: 6px 0 0; padding-left: 18px; font-size: 12.5px; color: #334155; line-height: 1.6; }}
  @media (max-width: 820px) {{ .rec-grid {{ grid-template-columns: 1fr; }} }}
  code.ik {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 10.5px;
              background: #f1f5f9; padding: 1px 5px; border-radius: 3px; color: #475569; }}
  .ch-badge {{ display: inline-block; background: #6366f1; color: #fff; padding: 2px 8px;
                border-radius: 4px; font-size: 11px; font-weight: 700; }}
  a.post-open {{ display: inline-block; margin-top: 6px; font-size: 11px; font-weight: 700;
                  color: #6366f1; white-space: nowrap; }}
  .post-caption {{ max-width: 360px; font-size: 12px; color: #475569; }}
  select.attr-select {{ width: 100%; padding: 7px 8px; border: 1px solid #e2e8f0;
                         border-radius: 6px; font-size: 12px; background: #fff; }}
  .ott-btn {{ background: #6366f1; color: #fff; border: none; border-radius: 8px;
               padding: 10px 16px; font-size: 13px; font-weight: 700; cursor: pointer; }}
  .ott-btn:hover {{ background: #4f46e5; }}
  .ott-btn-ghost {{ background: #fff; color: #6366f1; border: 1px solid #c7d2fe; }}
  .ott-btn-ghost:hover {{ background: #eef2ff; }}
  .ott-output {{ width: 100%; min-height: 90px; font-family: ui-monospace, Menlo, monospace;
                  font-size: 12px; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;
                  box-sizing: border-box; background: #f8fafc; }}
  .subhead {{ display: block; font-weight: 400; font-size: 9px; color: #94a3b8;
              text-transform: none; letter-spacing: 0; }}
  h3 {{ font-size: 14px; margin: 26px 0 12px; color: #475569;
        text-transform: uppercase; letter-spacing: .05em; }}
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

  {recommendations_html}

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

  <h3>All channels — total impressions per event</h3>
  {unified_channel_html}
  <div class="note">
    Total impressions = <b>paid FB ad impressions</b> + <b>Instagram organic views</b> + <b>TikTok organic views</b>.
    ⚠️ marks events with paid ad spend but no organic post coverage — a clear "we need to post more" signal.
    Organic posts are attributed by caption keywords (same rules as ad-set names).
  </div>

  <h3>Paid efficiency (FB ads only)</h3>
  {marketing_efficiency_html}
  <div class="note">
    <b>Imp / ticket</b> = paid ad impressions ÷ tickets sold (lower = more efficient creative).
    <b>CPA</b> = ad spend per ticket sold. <b>ROAS</b> = revenue ÷ ad spend.
    Campaigns/ad-sets are attributed by name to event series; data from Windsor.ai → Facebook Ads.
  </div>

  <h3>Impressions pace — by event</h3>
  <div class="chart-box">{marketing_pace_html}</div>

  <h3>Campaign breakdown — by event</h3>
  <p class="caption">Click any event to expand its campaign list and see which ads delivered the impressions.</p>
  {campaign_breakdown_html}

  <h2>Review queue — posts needing attribution confirmation</h2>
  {review_queue_html}


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
