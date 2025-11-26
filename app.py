"""
CrimeCompare - Modern Async Version
Compare crime statistics between two postcodes with a refined UI
"""

import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, datetime
from typing import List
import requests

from config import settings
from supabase_utils import init_supabase, get_supabase_client, supabase_available
from fingerprint_utils import get_user_id
from quota_manager import check_limit, increment_usage
from feedback_manager import save_feedback
from token_manager import get_settings_row
from crime_api_async import (
    fetch_both_postcodes_async,
    make_circle_polygon,
    summarize_crimes,
    format_category_name,
    get_risk_level,
    get_risk_badge_class,
    run_async,
    fetch_polygon_multiple_months_async,
    fetch_polygon_monthly_data_async
)

# ======================== PAGE CONFIG ========================
st.set_page_config(
    page_title="CrimeCompare",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ======================== LOAD CSS ========================
def load_css():
    """Load custom CSS styles"""
    css_path = "style.css"
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    else:
        st.warning("⚠️ style.css not found. Using default styling.")

load_css()

# ======================== HELPER FUNCTIONS ========================
def show_skeleton_card():
    """Display loading skeleton card"""
    st.markdown(
        """
        <div class="result-card skeleton-card">
            <div class="skeleton-line short"></div>
            <div class="skeleton-line long"></div>
        </div>
        """,
        unsafe_allow_html=True
    )

def parse_radius_setting(radius_str: str) -> float:
    """
    Parse radius setting to meters.
    
    Args:
        radius_str: String like "5 minutes", "1 mile"
        
    Returns:
        Distance in meters
    """
    if "minute" in radius_str:
        minutes = int(radius_str.split()[0])
        WALK_SPEED_M_PER_MIN = 80  # Average walking speed
        return minutes * WALK_SPEED_M_PER_MIN
    elif "mile" in radius_str:
        miles = float(radius_str.split()[0])
        return miles * 1609.34
    else:
        return 805  # Default: 0.5 miles


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_latest_month_from_api() -> str:
    """Fetch the latest available crime data month from Police.uk."""
    resp = requests.get("https://data.police.uk/api/crime-last-updated", timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    latest_date = payload.get("date")
    if not latest_date:
        raise ValueError("No date field returned from crime-last-updated endpoint")
    dt = datetime.fromisoformat(latest_date)
    return dt.strftime("%Y-%m")


def get_latest_month_with_fallback() -> str:
    """Get latest month, falling back to current month on error."""
    try:
        return fetch_latest_month_from_api()
    except Exception as e:
        st.warning(f"Using fallback month due to fetch error: {e}")
        today = date.today()
        return today.strftime("%Y-%m")


def build_recent_months(latest_month: str, count: int = 3) -> List[str]:
    """Return a list of recent months (YYYY-MM) ending at latest_month."""
    dt = datetime.strptime(latest_month, "%Y-%m")
    months: list[str] = []
    for offset in range(count):
        month = dt.month - offset
        year = dt.year
        while month <= 0:
            month += 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")
    return list(reversed(months))


def format_month_label(ym: str) -> str:
    """Convert YYYY-MM to 'Month YYYY'."""
    try:
        dt = datetime.strptime(ym, "%Y-%m")
        return dt.strftime("%B %Y")
    except Exception:
        return ym


def format_month_range(months: List[str]) -> str:
    """Format list of months to readable range."""
    if not months:
        return ""
    if len(months) == 1:
        return format_month_label(months[0])
    return f"{format_month_label(months[0])} to {format_month_label(months[-1])}"

# ======================== SESSION STATE INITIALIZATION ========================
if "loading" not in st.session_state:
    st.session_state.loading = False

if "results" not in st.session_state:
    st.session_state.results = None

if "supabase_initialized" not in st.session_state:
    init_supabase()
    st.session_state["supabase_initialized"] = True

if "has_run" not in st.session_state:
    st.session_state.has_run = False

if "latest_month" not in st.session_state:
    latest = get_latest_month_with_fallback()
    st.session_state["latest_month"] = latest
    st.session_state["quarter_months"] = build_recent_months(latest, 3)

latest_month = st.session_state.get("latest_month", get_latest_month_with_fallback())
quarter_months = st.session_state.get("quarter_months", build_recent_months(latest_month, 3))

# ======================== CHECK DATABASE & SERVICE STATUS ========================
if not supabase_available():
    st.error("⚠️ Database connection failed. Please check your configuration.")
    st.stop()

# ======================== ADMIN MODE ========================
query_params = st.query_params
admin_key_env = os.getenv("ADMIN_KEY")
is_admin = ("admin" in query_params and admin_key_env 
            and query_params["admin"] == admin_key_env)

if is_admin:
    st.title("🛠 Admin Panel")
    
    try:
        settings_row = get_settings_row()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Service", "⛔ Paused" if settings_row["service_paused"] else "✅ Active")
        col2.metric("Budget", f"${settings_row['monthly_budget_limit']}")
        col3.metric("Spent", f"${round(settings_row['total_spent'], 4)}")
        
        st.markdown("---")
        
        if st.button("Toggle Service", width="stretch"):
            supabase = get_supabase_client()
            new_val = not settings_row["service_paused"]
            supabase.table("settings").update({"service_paused": new_val}).eq("id", 1).execute()
            st.success(f"Service {'paused' if new_val else 'resumed'}")
            st.rerun()
            
    except Exception as e:
        st.error(f"Admin error: {e}")
    
    st.stop()

# ======================== USER CHECKS ========================
user_id = get_user_id()

try:
    settings_row = get_settings_row()
except Exception as e:
    st.error(f"⚠️ Failed to load settings: {e}")
    st.stop()

if settings_row["service_paused"]:
    st.error("⛔ Service temporarily paused for maintenance.")
    st.stop()

try:
    if check_limit(user_id):
        st.warning("⏳ Daily limit reached. Try again tomorrow.")
        st.stop()
except Exception as e:
    st.error(f"Failed to check quota: {e}")
    st.stop()

# ======================== HEADER ========================
st.markdown(
    """
    <div class="header">
        <h1>🔍 CrimeCompare</h1>
        <div class="subtitle">Crime data. Postcode clarity.</div>
    </div>
    """,
    unsafe_allow_html=True
)

# ======================== HERO SECTION ========================
st.markdown(
    """
    <div class="hero fade-in">
        <h2>Compare crime levels between two England areas</h2>
        <p>Enter two postcodes, set your search area, and see clear comparisons.</p>
    </div>
    """,
    unsafe_allow_html=True
)

# ======================== INPUT SECTION ========================
colA, colB, colRadius = st.columns([3, 3, 2], gap="large")

with colA:
    postcode_a = st.text_input(
        "📍 Postcode A", 
        placeholder="e.g. SW1A 1AA",
        help="Enter first postcode"
    )

with colB:
    postcode_b = st.text_input(
        "📍 Postcode B", 
        placeholder="e.g. E1 6AN",
        help="Enter second postcode"
    )

with colRadius:
    radius = st.selectbox(
        "Search area",
        ["5 minutes", "10 minutes", "15 minutes", "0.5 miles", "1 mile", "2 miles"],
        index=1,
        help="Walking time or direct radius"
    )

st.markdown(
    f"""
    <div style="text-align:center; margin-top:-0.5rem; margin-bottom:0.5rem;">
        <strong>Latest data month:</strong> {format_month_label(latest_month)} &nbsp;|&nbsp; <strong>Quarter window:</strong> {format_month_range(quarter_months)}
    </div>
    """,
    unsafe_allow_html=True
)

# ======================== RUN BUTTON ========================
run_cols = st.columns([3, 2, 3])
with run_cols[1]:
    run_clicked = st.button("Run", type="primary", width="stretch")

# ======================== HANDLE RUN CLICK ========================
if run_clicked:
    st.session_state.has_run = True
    # Validate inputs
    if not postcode_a or not postcode_b:
        st.error("❌ Please enter both postcodes")
        st.stop()
    
    postcode_a = postcode_a.strip().upper()
    postcode_b = postcode_b.strip().upper()
    
    if postcode_a == postcode_b:
        st.error("❌ Please enter two different postcodes")
        st.stop()
    
    # Reset results and trigger loading
    st.session_state.results = None
    st.session_state.loading = True
    st.rerun()

# ======================== RESULTS SECTION ========================
if st.session_state.has_run:
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ======================== LOADING STATE ========================
    if st.session_state.loading and st.session_state.results is None:
        # Show skeleton loaders
        col1, col2, col3, col4 = st.columns(4, gap="medium")
        with col1:
            show_skeleton_card()
        with col2:
            show_skeleton_card()
        with col3:
            show_skeleton_card()
        with col4:
            show_skeleton_card()

        try:
            increment_usage(user_id)
            supabase = get_supabase_client()
            res_a = supabase.table("postcodes").select("*").eq("postcode", postcode_a).maybe_single().execute()
            res_b = supabase.table("postcodes").select("*").eq("postcode", postcode_b).maybe_single().execute()
            if not res_a.data:
                st.session_state.results = {"error": f"Postcode {postcode_a} not found"}
                st.session_state.loading = False
                st.rerun()
            if not res_b.data:
                st.session_state.results = {"error": f"Postcode {postcode_b} not found"}
                st.session_state.loading = False
                st.rerun()

            lat_a, lng_a = res_a.data["lat"], res_a.data["lng"]
            lat_b, lng_b = res_b.data["lat"], res_b.data["lng"]
            radius_meters = parse_radius_setting(radius)
            polygon_a = make_circle_polygon(lat_a, lng_a, radius_meters)
            polygon_b = make_circle_polygon(lat_b, lng_b, radius_meters)

            events_a, events_b = run_async(
                fetch_both_postcodes_async(
                    {"postcode": postcode_a, "lat": lat_a, "lng": lng_a},
                    {"postcode": postcode_b, "lat": lat_b, "lng": lng_b},
                    polygon_a,
                    polygon_b,
                    latest_month
                )
            )

            quarter_events_a = run_async(fetch_polygon_multiple_months_async(polygon_a, quarter_months))
            quarter_events_b = run_async(fetch_polygon_multiple_months_async(polygon_b, quarter_months))
            monthly_events_a = run_async(fetch_polygon_monthly_data_async(polygon_a, quarter_months))
            monthly_events_b = run_async(fetch_polygon_monthly_data_async(polygon_b, quarter_months))

            summary_a = summarize_crimes(events_a)
            summary_b = summarize_crimes(events_b)
            quarter_summary_a = summarize_crimes(quarter_events_a)
            quarter_summary_b = summarize_crimes(quarter_events_b)
            monthly_summaries_a = {m: summarize_crimes(ev) for m, ev in monthly_events_a.items()}
            monthly_summaries_b = {m: summarize_crimes(ev) for m, ev in monthly_events_b.items()}

            st.session_state.results = {
                "postcode_a": postcode_a,
                "postcode_b": postcode_b,
                "a": {
                    "total": summary_a["total_crimes"],
                    "violent": summary_a["by_category"].get("violence-and-sexual-offences", 0),
                    "burglary": summary_a["by_category"].get("burglary", 0),
                    "risk_score": summary_a["risk_score"],
                    "risk_level": get_risk_level(summary_a["risk_score"]),
                    "by_category": summary_a["by_category"]
                },
                "b": {
                    "total": summary_b["total_crimes"],
                    "violent": summary_b["by_category"].get("violence-and-sexual-offences", 0),
                    "burglary": summary_b["by_category"].get("burglary", 0),
                    "risk_score": summary_b["risk_score"],
                    "risk_level": get_risk_level(summary_b["risk_score"]),
                    "by_category": summary_b["by_category"]
                },
                "quarter": {
                    "months": quarter_months,
                    "a": {
                        "total": quarter_summary_a["total_crimes"],
                        "violent": quarter_summary_a["by_category"].get("violence-and-sexual-offences", 0),
                        "burglary": quarter_summary_a["by_category"].get("burglary", 0),
                        "risk_score": quarter_summary_a["risk_score"],
                        "risk_level": get_risk_level(quarter_summary_a["risk_score"]),
                        "by_category": quarter_summary_a["by_category"]
                    },
                    "b": {
                        "total": quarter_summary_b["total_crimes"],
                        "violent": quarter_summary_b["by_category"].get("violence-and-sexual-offences", 0),
                        "burglary": quarter_summary_b["by_category"].get("burglary", 0),
                        "risk_score": quarter_summary_b["risk_score"],
                        "risk_level": get_risk_level(quarter_summary_b["risk_score"]),
                        "by_category": quarter_summary_b["by_category"]
                    }
                },
                "month": latest_month,
                "radius": radius,
                "monthly": {
                    "months": quarter_months,
                    "a": monthly_summaries_a,
                    "b": monthly_summaries_b
                }
            }

        except Exception as e:
            st.session_state.results = {"error": str(e)}

        st.session_state.loading = False
        st.rerun()

    # ======================== DISPLAY RESULTS ========================
    elif st.session_state.results is not None:
        data = st.session_state.results
        if "error" in data:
            st.error(f"Error: {data['error']}")
            st.stop()

        info_content = "\n".join([
            "- Total crimes: count of all reported incidents inside the chosen radius and time window.",
            "- Risk score: sum of (count x severity weight) per category, normalized to 0-100.",
            "- Higher severity (e.g. violence/sexual offences) contributes more to the score."
        ])
        info_title = info_content.replace("\n", "&#10;")
        st.markdown(
            f"""
            <div class="results-header">
                <h4 class="results-title">Results</h4>
                <span class="info-dot subtle" title="{info_title}">?</span>
            </div>
            """,
            unsafe_allow_html=True
        )

        tab_month, tab_quarter = st.tabs(["By Months", "Last Quarter"])

        with tab_month:
            month_options = data.get("monthly", {}).get("months", [data["month"]])
            default_month = month_options.index(data["month"]) if data["month"] in month_options else len(month_options) - 1
            month_cols = st.columns([1, 6, 1])
            selected_month = data["month"]
            with month_cols[1]:
                selected_month = st.selectbox(
                    "Month",
                    month_options,
                    index=default_month,
                    key="month_select",
                    format_func=format_month_label
                )
            m_a = data.get("monthly", {}).get("a", {}).get(selected_month, {"total_crimes": 0, "risk_score": 0, "by_category": {}})
            m_b = data.get("monthly", {}).get("b", {}).get(selected_month, {"total_crimes": 0, "risk_score": 0, "by_category": {}})

            label_cols = st.columns(2)
            label_cols[0].markdown(f"<div class='postcode-label large'>{data['postcode_a']}</div>", unsafe_allow_html=True)
            label_cols[1].markdown(f"<div class='postcode-label large'>{data['postcode_b']}</div>", unsafe_allow_html=True)
            col_left, col_right = st.columns(2, gap="medium")
            with col_left:
                for title, value in [("Total Crimes", m_a.get("total_crimes", 0)), ("Risk Score", m_a.get("risk_score", 0))]:
                    st.markdown(
                        f"""
                        <div class="result-card compact fade-in">
                            <h4>{title}</h4>
                            <p>{value}</p>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
            with col_right:
                for title, value in [("Total Crimes", m_b.get("total_crimes", 0)), ("Risk Score", m_b.get("risk_score", 0))]:
                    st.markdown(
                        f"""
                        <div class="result-card compact fade-in">
                            <h4>{title}</h4>
                            <p>{value}</p>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            st.markdown("<div style='margin-top: 1.0rem;'></div>", unsafe_allow_html=True)
            all_categories = set(m_a.get('by_category', {}).keys()) | set(m_b.get('by_category', {}).keys())
            category_data = []
            for cat in all_categories:
                category_data.append({
                    'Category': format_category_name(cat),
                    data['postcode_a']: m_a.get('by_category', {}).get(cat, 0),
                    data['postcode_b']: m_b.get('by_category', {}).get(cat, 0)
                })
            if category_data:
                df_cat = pd.DataFrame(category_data).sort_values(by=data['postcode_a'], ascending=False)
            else:
                df_cat = pd.DataFrame({'Category': [], data['postcode_a']: [], data['postcode_b']: []})
            fig_cat = go.Figure()
            fig_cat.add_trace(go.Bar(
                name=data['postcode_a'],
                x=df_cat['Category'],
                y=df_cat.get(data['postcode_a'], []),
                marker_color='#2d6a4f',
                hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'
            ))
            fig_cat.add_trace(go.Bar(
                name=data['postcode_b'],
                x=df_cat['Category'],
                y=df_cat.get(data['postcode_b'], []),
                marker_color='#ff7043',
                hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'
            ))
            fig_cat.update_layout(
                title="Crime Types Side-by-Side",
                xaxis_title="",
                yaxis_title="Incidents",
                barmode='group',
                height=420,
                xaxis_tickangle=-45,
                paper_bgcolor='white',
                plot_bgcolor='#fafafa',
                font=dict(family='Inter, sans-serif', size=12)
            )
            st.plotly_chart(fig_cat, width="stretch", key="category_chart")

            trend_categories = set()
            for month_key, summary in data.get('monthly', {}).get('a', {}).items():
                trend_categories.update(summary.get('by_category', {}).keys())
            for month_key, summary in data.get('monthly', {}).get('b', {}).items():
                trend_categories.update(summary.get('by_category', {}).keys())
            trend_options = ["All categories"] + sorted(trend_categories)
            trend_labels = {opt: ("All categories" if opt == "All categories" else format_category_name(opt)) for opt in trend_options}
            trend_cols = st.columns([1, 6, 1])
            with trend_cols[1]:
                selected_trend = st.selectbox(
                    "Trend by category (last 3 months)",
                    trend_options,
                    format_func=lambda x: trend_labels[x],
                    index=0,
                )
            trend_rows = []
            for month_key in data.get('monthly', {}).get('months', []):
                summary_a = data['monthly']['a'].get(month_key, {})
                summary_b = data['monthly']['b'].get(month_key, {})
                if selected_trend == "All categories":
                    count_a = summary_a.get('total_crimes', 0)
                    count_b = summary_b.get('total_crimes', 0)
                else:
                    count_a = summary_a.get('by_category', {}).get(selected_trend, 0)
                    count_b = summary_b.get('by_category', {}).get(selected_trend, 0)
                trend_rows.append({"Month": format_month_label(month_key), "Postcode": data['postcode_a'], "Count": count_a})
                trend_rows.append({"Month": format_month_label(month_key), "Postcode": data['postcode_b'], "Count": count_b})
            trend_df = pd.DataFrame(trend_rows)
            fig_trend = go.Figure()
            fig_trend.add_trace(go.Scatter(
                x=trend_df[trend_df['Postcode'] == data['postcode_a']]['Month'],
                y=trend_df[trend_df['Postcode'] == data['postcode_a']]['Count'],
                mode='lines+markers',
                name=data['postcode_a'],
                line=dict(color='#2d6a4f', width=3),
                marker=dict(size=8)
            ))
            fig_trend.add_trace(go.Scatter(
                x=trend_df[trend_df['Postcode'] == data['postcode_b']]['Month'],
                y=trend_df[trend_df['Postcode'] == data['postcode_b']]['Count'],
                mode='lines+markers',
                name=data['postcode_b'],
                line=dict(color='#ff7043', width=3),
                marker=dict(size=8)
            ))
            fig_trend.update_layout(
                title=f"{trend_labels.get(selected_trend)}",
                xaxis_title="",
                yaxis_title="Incidents",
                height=420,
                paper_bgcolor='white',
                plot_bgcolor='#fafafa',
                font=dict(family='Inter, sans-serif', size=12)
            )
            fig_trend.update_xaxes(
                type="category",
                categoryorder="array",
                categoryarray=[format_month_label(m) for m in data.get('monthly', {}).get('months', [])],
                tickvals=[format_month_label(m) for m in data.get('monthly', {}).get('months', [])],
                ticktext=[format_month_label(m) for m in data.get('monthly', {}).get('months', [])]
            )
            st.plotly_chart(fig_trend, width="stretch", key="trend_chart")

        with tab_quarter:
            st.caption(f"Search area: {data['radius']}")
            range_label = format_month_range(data['quarter']['months'])
            st.caption(f"Covering {range_label}")
            fig_q_total = go.Figure(data=[
                go.Bar(
                    x=[data['postcode_a'], data['postcode_b']],
                    y=[data['quarter']['a']['total'], data['quarter']['b']['total']],
                    marker_color=['#006d77', '#ffb703'],
                    text=[data['quarter']['a']['total'], data['quarter']['b']['total']],
                    textposition='auto',
                    hovertemplate='<b>%{x}</b><br>Quarter total crimes: %{y}<extra></extra>'
                )
            ])
            fig_q_total.update_layout(
                title="Total Crimes",
                xaxis_title="",
                yaxis_title="Incidents",
                height=420,
                showlegend=False,
                paper_bgcolor='white',
                plot_bgcolor='#fafafa',
                font=dict(family='Inter, sans-serif', size=12)
            )
            st.plotly_chart(fig_q_total, width="stretch", key="quarter_total_chart")

            quarter_categories = set(data['quarter']['a']['by_category'].keys()) | set(data['quarter']['b']['by_category'].keys())
            quarter_category_data = []
            for cat in quarter_categories:
                quarter_category_data.append({
                    'Category': format_category_name(cat),
                    data['postcode_a']: data['quarter']['a']['by_category'].get(cat, 0),
                    data['postcode_b']: data['quarter']['b']['by_category'].get(cat, 0)
                })
            if quarter_category_data:
                df_q_cat = pd.DataFrame(quarter_category_data).sort_values(by=data['postcode_a'], ascending=False)
            else:
                df_q_cat = pd.DataFrame({'Category': [], data['postcode_a']: [], data['postcode_b']: []})
            fig_q_cat = go.Figure()
            fig_q_cat.add_trace(go.Bar(
                name=data['postcode_a'],
                x=df_q_cat['Category'],
                y=df_q_cat[data['postcode_a']],
                marker_color='#118ab2',
                hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'
            ))
            fig_q_cat.add_trace(go.Bar(
                name=data['postcode_b'],
                x=df_q_cat['Category'],
                y=df_q_cat[data['postcode_b']],
                marker_color='#ef476f',
                hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'
            ))
            fig_q_cat.update_layout(
                title="Crime Types Side-by-Side",
                xaxis_title="",
                yaxis_title="Incidents",
                barmode='group',
                height=460,
                xaxis_tickangle=-45,
                paper_bgcolor='white',
                plot_bgcolor='#fafafa',
                font=dict(family='Inter, sans-serif', size=12)
            )
            st.plotly_chart(fig_q_cat, width="stretch", key="quarter_category_chart")
    st.markdown(
        f"""
        <div class="text-summary fade-in">
            <h4>Summary</h4>
            <p>
                <strong>{data['postcode_a']}</strong> recorded <strong>{data['a']['total']} total crimes</strong> 
                in {format_month_label(selected_month)} with a risk score of <strong>{data['a']['risk_score']}</strong> 
                (<span class="badge {get_risk_badge_class(data['a']['risk_score'])}">{data['a']['risk_level']} Risk</span>).
                <br><br>
                <strong>{data['postcode_b']}</strong> recorded <strong>{data['b']['total']} total crimes</strong> 
                with a risk score of <strong>{data['b']['risk_score']}</strong> 
                (<span class="badge {get_risk_badge_class(data['b']['risk_score'])}">{data['b']['risk_level']} Risk</span>).
                <br><br>
                Violence-related incidents: <strong>{data['a']['violent']}</strong> vs <strong>{data['b']['violent']}</strong>.<br>
                Burglary incidents: <strong>{data['a']['burglary']}</strong> vs <strong>{data['b']['burglary']}</strong>.
                <br><br>
                Last quarter totals: <strong>{data['quarter']['a']['total']}</strong> vs <strong>{data['quarter']['b']['total']}</strong> 
                across {format_month_range(data['quarter']['months'])}.
                    <br><br>
                    <em>Data reflects crime within a {data['radius']} area around each postcode.</em>
                </p>
            </div>
            """,
            unsafe_allow_html=True
        )

# ======================== FEEDBACK SECTION ========================
st.markdown("---")
st.markdown("### 💬 Feedback")

with st.form("feedback_form"):
    top_cols = st.columns([1.5, 4])
    with top_cols[0]:
        rating_choice = st.radio(
            "Was this helpful?",
            ["👍", "👎"],
            horizontal=True,
            index=0,
            help="Quick thumbs feedback"
        )
        rating = 5 if "👍" in rating_choice else 1
    with top_cols[1]:
        feedback_text = st.text_area(
            "Comments",
            placeholder="Share your thoughts...",
            height=80,
            max_chars=200,
            help="Up to 200 characters",
            label_visibility="collapsed"
        )
    
    btn_cols = st.columns([3, 2, 3])
    with btn_cols[1]:
        submitted = st.form_submit_button("Submit", width="stretch")
    
    if submitted:
        try:
            save_feedback(user_id, rating, feedback_text or "", "crime_comparator")
            st.success("✅ Thank you for your feedback!")
        except Exception as e:
            st.error(f"Failed to save feedback: {e}")

# ======================== FOOTER ========================
st.markdown(
    f"""
    <div class="legal-card" style="margin-top: 1.5rem;">
        <h4>📜 Legal & Attribution</h4>
        <p>
            Contains public sector information licensed under the 
            <a href="http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" 
            target="_blank">Open Government Licence v3.0</a>.
            <br><br>
            <strong>Data Sources:</strong> 
            <a href="https://data.police.uk/" target="_blank">Police.uk API</a> | 
            <a href="https://geoportal.statistics.gov.uk/" target="_blank">ONS Postcode Directory</a>
            <br><br>
            <small>CrimeCompare &copy; 2025</small>
        </p>
    </div>
    """,
    unsafe_allow_html=True
)
