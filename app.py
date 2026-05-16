"""Streamlit dashboard for SweatPals ticket sales analytics. Run: streamlit run app.py"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.analytics import (
    CAPACITY_CSV, comparable_curve, daily_velocity, event_summary, load_capacities,
    load_tickets, load_waitlist, pace_flag, sales_curve, show_up_rates, write_capacity_template,
)

st.set_page_config(page_title="OTT Ticketing Dashboard", layout="wide", page_icon=":bar_chart:")


@st.cache_data(ttl=60)
def get_data():
    return load_tickets(), load_capacities(), load_waitlist()


def fmt_money(v) -> str:
    if pd.isna(v):
        return "—"
    return f"${v:,.0f}"


def render_pipeline_health(tickets, capacities, waitlist):
    st.subheader("Pipeline health — upcoming events")
    summary = event_summary(tickets, capacities, waitlist)
    if summary.empty:
        st.info("No ticket sales yet. Run `python -m src.sync` to pull data.")
        return
    upcoming = summary[summary["status"] != "past"].copy()
    if upcoming.empty:
        st.info("No upcoming events on sale right now.")
        return

    flags, reasons = [], []
    for _, row in upcoming.iterrows():
        comp = comparable_curve(tickets, row)
        flag, reason = pace_flag(row, comp)
        flags.append(flag)
        reasons.append(reason)
    upcoming["pace"] = flags
    upcoming["pace_reason"] = reasons

    badge = {"behind": "🔴 BEHIND", "on_pace": "🟢 ON PACE", "ahead": "🔵 AHEAD",
             "no_baseline": "⚪ NO BASELINE", "past": "—"}
    upcoming["pace_label"] = upcoming["pace"].map(badge)

    cols = st.columns(5)
    cols[0].metric("Upcoming events", len(upcoming))
    cols[1].metric("Tickets sold (upcoming)", int(upcoming["tickets_sold"].sum()))
    cols[2].metric("Expected attendance", int(upcoming["expected_attendance"].sum()))
    cols[3].metric("Upcoming revenue", fmt_money(upcoming["net_revenue"].sum()))
    behind_count = int((upcoming["pace"] == "behind").sum())
    cols[4].metric("⚠️ Behind pace", behind_count, delta_color="inverse")

    display = upcoming[[
        "event_name", "event_instance_date", "days_until_event",
        "paid_tickets", "free_tickets", "tickets_sold", "expected_attendance", "capacity",
        "net_revenue", "waitlist_count", "pace_label", "pace_reason",
    ]].rename(columns={
        "event_name": "Event", "event_instance_date": "Date", "days_until_event": "Days out",
        "paid_tickets": "Paid", "free_tickets": "Free", "tickets_sold": "Total",
        "expected_attendance": "Expected attend",
        "capacity": "Cap", "net_revenue": "Revenue", "waitlist_count": "Waitlist",
        "pace_label": "Pace", "pace_reason": "Why",
    })
    display["Date"] = pd.to_datetime(display["Date"]).dt.strftime("%b %d, %Y")
    display["Revenue"] = display["Revenue"].apply(fmt_money)
    display["Cap"] = display["Cap"].apply(lambda v: int(v) if pd.notna(v) else "—")
    display["Paid"] = display["Paid"].astype(int)
    display["Free"] = display["Free"].astype(int)
    st.dataframe(display, hide_index=True, use_container_width=True)

    rates = show_up_rates(tickets)
    paid_n = rates.get("paid_n", 0)
    free_n = rates.get("free_n", 0)
    paid_label = f"{rates['paid']*100:.0f}% (n={paid_n} past tickets)" if paid_n else f"{rates['paid']*100:.0f}% (industry default)"
    free_label = f"{rates['free']*100:.0f}% (n={free_n} past tickets)" if free_n else f"{rates['free']*100:.0f}% (industry default)"
    st.caption(
        f"**Expected attendance** = paid sold × paid show-up rate **+** free sold × free show-up rate. "
        f"OTT historical rates: paid **{paid_label}**, free **{free_label}**. "
        f"For event series with ≥100 past tickets, the dashboard uses the series' own rate. "
        f"**Pace** compares sell-through against past instances of the same series at the same days-out."
    )


def render_event_detail(tickets, capacities, waitlist):
    st.subheader("Event detail")
    summary = event_summary(tickets, capacities, waitlist)
    if summary.empty:
        st.info("Sync data first.")
        return

    options = summary.sort_values("event_instance_date", ascending=False).copy()
    options["label"] = options.apply(
        lambda r: f"{r['event_name']} — {pd.to_datetime(r['event_instance_date']).strftime('%b %d, %Y')} ({int(r['tickets_sold'])} tix)",
        axis=1,
    )
    pick = st.selectbox("Pick an event", options=options["label"].tolist())
    row = options[options["label"] == pick].iloc[0]

    cols = st.columns(5)
    cols[0].metric("Tickets sold", int(row["tickets_sold"]))
    cols[1].metric("Paid / Free", f"{int(row['paid_tickets'])} / {int(row['free_tickets'])}")
    if row["status"] == "past":
        cols[2].metric("Actual attended", int(row["attended_count"]))
        cols[3].metric("Show-up rate", f"{row['attendance_rate_pct']:.0f}%" if pd.notna(row["attendance_rate_pct"]) else "—")
    else:
        cols[2].metric("Expected attend.", int(row["expected_attendance"]))
        cols[3].metric("Capacity", int(row["capacity"]) if pd.notna(row["capacity"]) else "—")
    cols[4].metric(
        "Days until event" if row["status"] != "past" else "Days since event",
        abs(int(row["days_until_event"])) if pd.notna(row["days_until_event"]) else "—",
    )

    cols2 = st.columns(4)
    cols2[0].metric("Net revenue", fmt_money(row["net_revenue"]))
    cols2[1].metric("Unique orders", int(row["unique_orders"]))
    cols2[2].metric("Avg tix/order", f"{row['tickets_sold']/row['unique_orders']:.2f}" if row["unique_orders"] else "—")
    cols2[3].metric("Waitlist" if row["status"] != "past" else "Discounts given",
                    int(row["waitlist_count"]) if row["status"] != "past" else fmt_money(row["discounts"]))

    if row["status"] != "past":
        paid_pct = (row["paid_rate_used"] or 0) * 100
        free_pct = (row["free_rate_used"] or 0) * 100
        st.caption(
            f"Expected attendance assumes **{paid_pct:.0f}%** paid show-up × {int(row['paid_tickets'])} paid + "
            f"**{free_pct:.0f}%** free show-up × {int(row['free_tickets'])} free. Source: `{row['rate_source']}`."
        )

    curve = sales_curve(tickets, row["instance_key"])
    comp = comparable_curve(tickets, row)

    if curve.empty:
        st.info("No sales yet for this event.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=curve["days_before_event"], y=curve["tickets_cum"],
        mode="lines+markers", name="This event (tickets sold)",
        line=dict(width=3),
    ))

    if not comp.empty and pd.notna(row["capacity"]):
        comp_tix = comp.copy()
        comp_tix["expected_tickets"] = comp_tix["tickets_pct_of_final"] / 100 * row["capacity"]
        fig.add_trace(go.Scatter(
            x=comp_tix["days_before_event"], y=comp_tix["expected_tickets"],
            mode="lines", name=f"Avg of past {len(comp_tix['days_before_event'].unique())} comparable runs",
            line=dict(dash="dash"),
        ))
    elif not comp.empty:
        st.caption("(Set this event's capacity in the **Capacity config** tab to overlay the historical pace curve.)")

    fig.update_xaxes(autorange="reversed", title="Days before event →")
    fig.update_yaxes(title="Cumulative tickets sold")
    fig.update_layout(height=420, hovermode="x unified", margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    flag, reason = pace_flag(row, comp)
    if flag == "behind":
        st.error(f"**Behind pace** — {reason}")
    elif flag == "ahead":
        st.success(f"**Ahead of pace** — {reason}")
    elif flag == "on_pace":
        st.info(f"**On pace** — {reason}")
    else:
        st.warning(reason)


def render_portfolio(tickets):
    st.subheader("Portfolio overview")
    if tickets.empty:
        return

    cols = st.columns(4)
    cols[0].metric("Lifetime revenue", fmt_money(tickets["net_price"].sum()))
    cols[1].metric("Total tickets sold", len(tickets))
    cols[2].metric("Distinct events run", tickets["instance_key"].nunique())
    avg_per_event = tickets.groupby("instance_key")["net_price"].sum().mean()
    cols[3].metric("Avg revenue / event", fmt_money(avg_per_event))

    by_event = (
        tickets.groupby(["event_name"], as_index=False)
        .agg(tickets=("id", "count"), revenue=("net_price", "sum"), instances=("instance_key", "nunique"))
        .sort_values("revenue", ascending=False)
        .head(15)
    )
    fig = px.bar(by_event, x="event_name", y="revenue", title="Top 15 event series by total revenue",
                 hover_data=["tickets", "instances"])
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=40, b=10))
    fig.update_xaxes(tickangle=-30)
    st.plotly_chart(fig, use_container_width=True)

    by_tier = tickets.groupby("event_price_tier_name", as_index=False).agg(
        tickets=("id", "count"), revenue=("net_price", "sum")
    ).sort_values("revenue", ascending=False).head(20)
    if not by_tier.empty:
        st.caption("Revenue by price tier")
        st.dataframe(by_tier, hide_index=True, use_container_width=True)


def render_velocity(tickets):
    st.subheader("Sales velocity")
    days_window = st.slider("Lookback (days)", min_value=7, max_value=180, value=30, step=7)
    daily = daily_velocity(tickets, days=days_window)
    if daily.empty:
        st.info("No recent sales.")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(x=daily["day"], y=daily["tickets"], name="Tickets", yaxis="y"))
    fig.add_trace(go.Scatter(x=daily["day"], y=daily["revenue"], name="Revenue ($)",
                             yaxis="y2", mode="lines+markers"))
    fig.update_layout(
        height=400, hovermode="x unified",
        yaxis=dict(title="Tickets sold"),
        yaxis2=dict(title="Revenue ($)", overlaying="y", side="right"),
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    summary_cols = st.columns(3)
    summary_cols[0].metric(f"Tickets sold ({days_window}d)", int(daily["tickets"].sum()))
    summary_cols[1].metric(f"Revenue ({days_window}d)", fmt_money(daily["revenue"].sum()))
    summary_cols[2].metric("Avg tickets/day", f"{daily['tickets'].mean():.1f}")


def render_capacity_admin(tickets):
    st.subheader("Capacity config")
    st.write(
        "Each event instance needs a capacity to enable sell-through % and on-pace tracking. "
        "Generate the template, then edit `config/capacities.csv` and reload."
    )
    cols = st.columns([1, 1])
    if cols[0].button("Generate / refresh capacities.csv from synced events"):
        n = write_capacity_template(tickets)
        st.success(f"Wrote {n} event instances to {CAPACITY_CSV.name} (existing capacities preserved)")
    if cols[1].button("Reload data"):
        get_data.clear()
        st.rerun()
    if CAPACITY_CSV.exists():
        st.code(str(CAPACITY_CSV), language="text")
        df = pd.read_csv(CAPACITY_CSV)
        n_filled = df["capacity"].apply(lambda v: pd.notna(v) and str(v).strip() != "").sum()
        st.caption(f"{n_filled} of {len(df)} instances have capacity set")
        st.dataframe(df, hide_index=True, use_container_width=True)


def main():
    st.title("OTT Ticketing Dashboard")
    tickets, capacities, waitlist = get_data()

    if tickets.empty:
        st.warning(
            "No data in the local database yet.\n\n"
            "Run **`python -m src.sync`** in your terminal first."
        )
        return

    with st.sidebar:
        st.markdown("**Data freshness**")
        latest = tickets["order_date"].max()
        st.caption(f"Most recent order: {latest:%Y-%m-%d %H:%M} UTC")
        st.caption(f"Tickets in DB: {len(tickets):,}")
        st.caption(f"Distinct events: {tickets['instance_key'].nunique():,}")
        if st.button("Clear cache & reload"):
            get_data.clear()
            st.rerun()

    tabs = st.tabs(["Pipeline health", "Event detail", "Portfolio", "Velocity", "Capacity config"])
    with tabs[0]:
        render_pipeline_health(tickets, capacities, waitlist)
    with tabs[1]:
        render_event_detail(tickets, capacities, waitlist)
    with tabs[2]:
        render_portfolio(tickets)
    with tabs[3]:
        render_velocity(tickets)
    with tabs[4]:
        render_capacity_admin(tickets)


if __name__ == "__main__":
    main()
