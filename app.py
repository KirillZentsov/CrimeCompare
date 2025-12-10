"""
CrimeCompare - Final Version with Optimized DB Search
Compare crime statistics between two postcodes
"""

import math
import os
import re
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.graph_objects as go
from datetime import date, datetime
from typing import List
import requests
import folium
from streamlit_folium import st_folium

from config import settings
from supabase_utils import init_supabase, supabase_available
from fingerprint_utils import get_user_id
from quota_manager import check_limit, increment_usage
from feedback_manager import save_feedback
from token_manager import get_settings_row
from crime_api_async import (
    make_circle_polygon, summarize_crimes, format_category_name,
    get_risk_level, get_risk_badge_class, run_async,
    fetch_both_postcodes_async, fetch_polygon_multiple_months_async,
    fetch_polygon_monthly_data_async, CRIME_WEIGHTS
)
from postcode_autocomplete import (
    normalize_postcode, postcode_exists, get_postcode_data,
    validate_postcode_pair, is_valid_postcode
)

# ======================== PAGE CONFIG ========================
st.set_page_config(
    page_title="CrimeCompare", page_icon="🛡️",
    layout="wide", initial_sidebar_state="collapsed"
)

# ======================== LOAD CSS ========================
def load_css():
    css_path = "style.css"
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css()

# ======================== HELPER FUNCTIONS ========================
def show_skeleton_card():
    st.markdown('<div class="result-card skeleton-card"><div class="skeleton-line short"></div><div class="skeleton-line long"></div></div>', unsafe_allow_html=True)

def parse_radius_setting(radius_str: str) -> float:
    if "minute" in radius_str:
        return int(radius_str.split()[0]) * 80
    elif "mile" in radius_str:
        return float(radius_str.split()[0]) * 1609.34
    return 805

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_latest_month_from_api() -> str:
    resp = requests.get("https://data.police.uk/api/crime-last-updated", timeout=10)
    resp.raise_for_status()
    return datetime.fromisoformat(resp.json()["date"]).strftime("%Y-%m")

def get_latest_month_with_fallback() -> str:
    try:
        return fetch_latest_month_from_api()
    except Exception:
        return date.today().strftime("%Y-%m")

def build_recent_months(latest_month: str, count: int = 3) -> List[str]:
    dt = datetime.strptime(latest_month, "%Y-%m")
    months = []
    for offset in range(count):
        month, year = dt.month - offset, dt.year
        while month <= 0:
            month += 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")
    return list(reversed(months))

def format_month_label(ym: str) -> str:
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
    except:
        return ym

def format_month_range(months: List[str]) -> str:
    if not months:
        return ""
    if len(months) == 1:
        return format_month_label(months[0])
    return f"{format_month_label(months[0])} to {format_month_label(months[-1])}"

def create_comparison_map(lat_a: float, lng_a: float, lat_b: float, lng_b: float, 
                          postcode_a: str, postcode_b: str, radius_meters: float) -> folium.Map:
    """Create an interactive map showing both postcodes with gradient search radii"""
    # Calculate center point between the two locations
    center_lat = (lat_a + lat_b) / 2
    center_lng = (lng_a + lng_b) / 2
    
    # Create base map with a clean style
    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=13,
        tiles='cartodbpositron',
        control_scale=False,  # Cleaner look
        zoom_control=True,
        min_zoom=6,
        max_zoom=18
    )
    
    # Colors matching our theme
    color_a = '#0d9488'  # Teal
    color_b = '#3b82f6'  # Blue
    
    # Gradient circles - multiple layers with decreasing opacity
    # Creates a soft, diffused effect without hard edges
    gradient_layers = [
        (1.0, 0.20),   # 100% radius, highest opacity (center)
        (0.85, 0.15),
        (0.70, 0.12),
        (0.55, 0.08),
        (0.40, 0.05),
        (0.25, 0.03),  # Smallest, most opaque core
    ]
    
    # Add gradient circles for Postcode A (from outside to inside)
    for radius_mult, opacity in gradient_layers:
        folium.Circle(
            location=[lat_a, lng_a],
            radius=radius_meters * radius_mult,
            color=color_a,
            fill=True,
            fillColor=color_a,
            fillOpacity=opacity,
            weight=0,  # No border
            stroke=False
        ).add_to(m)
    
    # Add gradient circles for Postcode B (from outside to inside)
    for radius_mult, opacity in gradient_layers:
        folium.Circle(
            location=[lat_b, lng_b],
            radius=radius_meters * radius_mult,
            color=color_b,
            fill=True,
            fillColor=color_b,
            fillOpacity=opacity,
            weight=0,  # No border
            stroke=False
        ).add_to(m)
    
    # Add marker for Postcode A
    folium.Marker(
        location=[lat_a, lng_a],
        popup=f"<b>{postcode_a}</b><br>Search radius: {radius_meters:.0f}m",
        tooltip=postcode_a,
        icon=folium.Icon(color='green', icon='home', prefix='fa')
    ).add_to(m)
    
    # Add marker for Postcode B
    folium.Marker(
        location=[lat_b, lng_b],
        popup=f"<b>{postcode_b}</b><br>Search radius: {radius_meters:.0f}m",
        tooltip=postcode_b,
        icon=folium.Icon(color='blue', icon='home', prefix='fa')
    ).add_to(m)
    
    # Fit map to show both postcodes with some padding
    m.fit_bounds([[lat_a, lng_a], [lat_b, lng_b]], padding=[50, 50], max_zoom=14)
    
    return m


def group_categories(by_category: dict) -> dict:
    """Group detailed categories into broader buckets."""
    buckets = {
        "Violence / weapons": {"violence-and-sexual-offences", "robbery", "possession-of-weapons"},
        "Property": {"burglary", "vehicle-crime", "other-theft", "shoplifting", "theft-from-the-person", "bicycle-theft"},
        "Public order / drugs": {"public-order", "drugs", "anti-social-behaviour"},
        "Criminal damage": {"criminal-damage-arson"},
    }
    grouped = {name: 0 for name in buckets.keys()}
    other_total = 0
    for cat, count in by_category.items():
        placed = False
        for name, cats in buckets.items():
            if cat in cats:
                grouped[name] += count
                placed = True
                break
        if not placed:
            other_total += count
    grouped["Other"] = other_total
    return grouped


def severity_buckets(by_category: dict) -> dict:
    """Split crimes into High/Medium/Low based on CRIME_WEIGHTS."""
    levels = {"High": 0, "Medium": 0, "Low": 0}
    for cat, count in by_category.items():
        weight = CRIME_WEIGHTS.get(cat, 1)
        if weight >= 8:
            levels["High"] += count
        elif weight >= 4:
            levels["Medium"] += count
        else:
            levels["Low"] += count
    return levels


def calc_density(total_crimes: int, radius_meters: float) -> int:
    """Crimes per km^2 based on circular search area."""
    if radius_meters <= 0:
        return 0
    area_km2 = math.pi * (radius_meters / 1000) ** 2
    return round(total_crimes / area_km2) if area_km2 > 0 else 0


def format_category_name(cat: str) -> str:
    """Format category name for display."""
    return cat.replace("-", " ").title()


def calc_safer_text(total_a: int, total_b: int, postcode_a: str, postcode_b: str) -> str:
    """Calculate which postcode is safer and by how much."""
    if total_a == 0 and total_b == 0:
        return "No crimes recorded in either area"
    
    if total_b > 0 and total_a < total_b:
        safer_pct = round((total_b - total_a) / total_b * 100)
        if safer_pct < 5:
            return f"Both areas show similar crime levels ({total_a} vs {total_b})"
        return f"🏆 <strong>{postcode_a}</strong> is {safer_pct}% safer with {total_a} vs {total_b} crimes"
    elif total_a > 0 and total_b < total_a:
        safer_pct = round((total_a - total_b) / total_a * 100)
        if safer_pct < 5:
            return f"Both areas show similar crime levels ({total_a} vs {total_b})"
        return f"🏆 <strong>{postcode_b}</strong> is {safer_pct}% safer with {total_b} vs {total_a} crimes"
    else:
        return f"Both areas show similar crime levels ({total_a} vs {total_b})"


def calc_top_category(by_category: dict, total: int) -> tuple:
    """Find top crime category and its percentage."""
    if not by_category or total == 0:
        return "N/A", 0
    top_cat = max(by_category, key=by_category.get)
    top_pct = round(by_category[top_cat] / total * 100)
    return format_category_name(top_cat), top_pct


def calc_key_differences(cat_a: dict, cat_b: dict, limit: int = 3) -> list:
    """Calculate top differences between two category dicts."""
    all_categories = set(cat_a.keys()) | set(cat_b.keys())
    differences = []
    
    for cat in all_categories:
        val_a = cat_a.get(cat, 0)
        val_b = cat_b.get(cat, 0)
        
        # Skip if both are zero
        if val_a == 0 and val_b == 0:
            continue
            
        # Calculate percentage difference
        if val_b > 0:
            diff_pct = round((val_a - val_b) / val_b * 100)
        elif val_a > 0:
            diff_pct = 100
        else:
            diff_pct = 0
            
        differences.append({
            "category": format_category_name(cat),
            "val_a": val_a,
            "val_b": val_b,
            "diff_pct": diff_pct
        })
    
    # Sort by absolute difference
    differences.sort(key=lambda x: abs(x["diff_pct"]), reverse=True)
    return differences[:limit]


def format_differences_html(differences: list) -> str:
    """Format differences list as HTML."""
    lines = []
    for d in differences:
        if d["diff_pct"] > 5:
            lines.append(f"• {d['category']}: {d['val_a']} vs {d['val_b']} (A is {d['diff_pct']}% higher)")
        elif d["diff_pct"] < -5:
            lines.append(f"• {d['category']}: {d['val_a']} vs {d['val_b']} (B is {abs(d['diff_pct'])}% higher)")
        else:
            lines.append(f"• {d['category']}: {d['val_a']} vs {d['val_b']} (similar)")
    return "<br>".join(lines) if lines else "No significant differences"


def calc_trend(monthly_data: dict, months: list) -> tuple:
    """Calculate trend from first to last month. Returns (percent, arrow_text)."""
    if len(months) < 2:
        return 0, "—"
    
    first_month = months[0]
    last_month = months[-1]
    
    first_val = monthly_data.get(first_month, {}).get("total_crimes", 0)
    last_val = monthly_data.get(last_month, {}).get("total_crimes", 0)
    
    if first_val == 0:
        return 0, "—"
    
    trend_pct = round((last_val - first_val) / first_val * 100)
    
    if trend_pct > 5:
        return trend_pct, f"↗️ +{trend_pct}%"
    elif trend_pct < -5:
        return trend_pct, f"↘️ {trend_pct}%"
    else:
        return trend_pct, "→ stable"


def get_monthly_values(monthly_data: dict, months: list) -> str:
    """Get monthly crime values as string like '40 → 43 → 45'."""
    values = []
    for m in months:
        val = monthly_data.get(m, {}).get("total_crimes", 0)
        values.append(str(val))
    return " → ".join(values)


def handle_postcode_input(input_key: str, selected_key: str, error_key: str) -> None:
    """Validate postcode input on blur/Enter and update session state."""
    raw_value = (st.session_state.get(input_key) or "").strip().upper()
    st.session_state[input_key] = raw_value
    st.session_state[error_key] = None
    st.session_state[selected_key] = None
    st.session_state.results = None
    st.session_state.has_run = False

    if not raw_value:
        return

    if not re.match(r"^[A-Z0-9 ]+$", raw_value):
        st.session_state[error_key] = f"{raw_value} not found"
        st.session_state[input_key] = ""
        return

    normalized = normalize_postcode(raw_value)

    if not is_valid_postcode(normalized):
        st.session_state[error_key] = f"{raw_value} not found"
        st.session_state[input_key] = ""
        return

    if not postcode_exists(normalized):
        st.session_state[error_key] = f"{raw_value} not found"
        st.session_state[input_key] = ""
        return

    st.session_state[selected_key] = normalized
    st.session_state[input_key] = normalized

# ======================== SESSION STATE ========================
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
# Selected postcodes for badge display
if "postcode_a_selected" not in st.session_state:
    st.session_state.postcode_a_selected = None
if "postcode_b_selected" not in st.session_state:
    st.session_state.postcode_b_selected = None
if "postcode_a_input" not in st.session_state:
    st.session_state.postcode_a_input = ""
if "postcode_b_input" not in st.session_state:
    st.session_state.postcode_b_input = ""
if "postcode_a_error" not in st.session_state:
    st.session_state.postcode_a_error = None
if "postcode_b_error" not in st.session_state:
    st.session_state.postcode_b_error = None
# Auto-scroll flag
if "should_scroll" not in st.session_state:
    st.session_state.should_scroll = False

latest_month = st.session_state.get("latest_month", get_latest_month_with_fallback())
quarter_months = st.session_state.get("quarter_months", build_recent_months(latest_month, 3))

# ======================== CHECKS ========================
if not supabase_available():
    st.error("⚠️ Database connection failed.")
    st.stop()

query_params = st.query_params
admin_key_env = os.getenv("ADMIN_KEY")
is_admin = "admin" in query_params and admin_key_env and query_params["admin"] == admin_key_env

if is_admin:
    st.title("🛠 Admin Panel")
    try:
        from supabase_utils import get_supabase_client
        settings_row = get_settings_row()
        col1, col2, col3 = st.columns(3)
        col1.metric("Service", "⛔ Paused" if settings_row["service_paused"] else "✅ Active")
        col2.metric("Budget", f"${settings_row['monthly_budget_limit']}")
        col3.metric("Spent", f"${round(settings_row['total_spent'], 4)}")
        st.markdown("---")
        if st.button("Toggle Service"):
            client = get_supabase_client()
            new_val = not settings_row["service_paused"]
            client.table("settings").update({"service_paused": new_val}).eq("id", 1).execute()
            st.success(f"Service {'paused' if new_val else 'resumed'}")
            st.rerun()
    except Exception as e:
        st.error(f"Admin error: {e}")
    st.stop()

user_id = get_user_id()
try:
    settings_row = get_settings_row()
except Exception as e:
    st.error(f"⚠️ Failed to load settings: {e}")
    st.stop()

if settings_row["service_paused"]:
    st.error("⛔ Service temporarily paused.")
    st.stop()

try:
    if check_limit(user_id):
        st.warning("⏳ Daily limit reached.")
        st.stop()
except Exception as e:
    st.error(f"Failed to check quota: {e}")
    st.stop()

# ======================== HEADER ========================
st.markdown('''
<div class="header">
    <h1>🛡️ CrimeCompare</h1>
    <div class="subtitle">See the real picture across England</div>
</div>
''', unsafe_allow_html=True)

# Trust bar
st.markdown('''
<div class="trust-bar fade-in">
    <div class="trust-item"><span class="icon">⚖️</span> Side by side</div>
    <div class="trust-item"><span class="icon">⚡</span> Most Current Police Data</div>
</div>
''', unsafe_allow_html=True)

# ======================== INPUT SECTION ========================
spacer1, colA, colB, colRadius, spacer2 = st.columns([1, 3, 3, 2, 1], gap="medium")

with colA:
    st.markdown('<p class="input-label">📍 Postcode A</p>', unsafe_allow_html=True)
    
    if st.session_state.postcode_a_selected:
        badge_col, btn_col = st.columns([5, 1])
        with badge_col:
            st.markdown(f'<div class="selected-badge badge-a"><span class="badge-icon">A</span> {st.session_state.postcode_a_selected}</div>', unsafe_allow_html=True)
        with btn_col:
            if st.button("X", key="clear_a", help="Change postcode"):
                st.session_state.postcode_a_selected = None
                st.session_state.postcode_a_input = ""
                st.session_state.postcode_a_error = None
                st.session_state.results = None
                st.session_state.has_run = False
                st.rerun()
    else:
        st.text_input(
            "",
            key="postcode_a_input",
            placeholder="CO2 7QG",
            max_chars=8,
            label_visibility="collapsed",
            on_change=handle_postcode_input,
            args=("postcode_a_input", "postcode_a_selected", "postcode_a_error")
        )
        if st.session_state.postcode_a_error:
            st.markdown(f'<p class="input-error">{st.session_state.postcode_a_error}</p>', unsafe_allow_html=True)

with colB:
    st.markdown('<p class="input-label">📍 Postcode B</p>', unsafe_allow_html=True)
    
    if st.session_state.postcode_b_selected:
        badge_col, btn_col = st.columns([5, 1])
        with badge_col:
            st.markdown(f'<div class="selected-badge badge-b"><span class="badge-icon">B</span> {st.session_state.postcode_b_selected}</div>', unsafe_allow_html=True)
        with btn_col:
            if st.button("X", key="clear_b", help="Change postcode"):
                st.session_state.postcode_b_selected = None
                st.session_state.postcode_b_input = ""
                st.session_state.postcode_b_error = None
                st.session_state.results = None
                st.session_state.has_run = False
                st.rerun()
    else:
        st.text_input(
            "",
            key="postcode_b_input",
            placeholder="DE1 1TQ",
            max_chars=8,
            label_visibility="collapsed",
            on_change=handle_postcode_input,
            args=("postcode_b_input", "postcode_b_selected", "postcode_b_error")
        )
        if st.session_state.postcode_b_error:
            st.markdown(f'<p class="input-error">{st.session_state.postcode_b_error}</p>', unsafe_allow_html=True)

with colRadius:
    st.markdown('<p class="input-label">📏 Search Area</p>', unsafe_allow_html=True)
    radius = st.selectbox(
        "Search Area",
        ["5 minutes", "10 minutes", "15 minutes", "0.5 miles", "1 mile", "2 miles"],
        index=1,
        help="Walking time or radius",
        label_visibility="collapsed"
    )

st.markdown(f'<div style="text-align:center; margin-top:0.5rem; margin-bottom:1rem;"><strong>Latest Month:</strong> {format_month_label(latest_month)} | <strong>Quarter:</strong> {format_month_range(quarter_months)}</div>', unsafe_allow_html=True)

run_cols = st.columns([3, 2, 3])
with run_cols[1]:
    run_clicked = st.button("Compare", type="primary", use_container_width=True)


# ======================== HANDLE RUN ========================
if run_clicked:
    st.session_state.has_run = True
    
    # Используем выбранные postcodes из session state
    postcode_a = st.session_state.postcode_a_selected
    postcode_b = st.session_state.postcode_b_selected
    
    # Проверить что выбраны оба postcodes
    if not postcode_a or not postcode_b:
        st.error("❌ Please select both postcodes")
        st.stop()
    
    actual_a = postcode_a.split(' - ')[0] if ' - ' in postcode_a else postcode_a
    actual_b = postcode_b.split(' - ')[0] if ' - ' in postcode_b else postcode_b
    
    normalized_a = normalize_postcode(actual_a)
    normalized_b = normalize_postcode(actual_b)
    
    is_valid, error_msg = validate_postcode_pair(normalized_a, normalized_b)
    if not is_valid:
        st.error(f"❌ {error_msg}")
        st.stop()
    
    st.session_state.results = None
    st.session_state.loading = True
    st.session_state.postcode_a_final = normalized_a
    st.session_state.postcode_b_final = normalized_b
    st.rerun()

# ======================== RESULTS ========================
if st.session_state.has_run:
    # Invisible anchor for auto-scroll
    st.markdown('<div id="results-anchor"></div>', unsafe_allow_html=True)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    postcode_a_final = st.session_state.get("postcode_a_final", "")
    postcode_b_final = st.session_state.get("postcode_b_final", "")
    
    if st.session_state.loading and st.session_state.results is None:
        col1, col2, col3, col4 = st.columns(4, gap="medium")
        for col in [col1, col2, col3, col4]:
            with col:
                show_skeleton_card()

        try:
            increment_usage(user_id)
            data_a = get_postcode_data(postcode_a_final)
            data_b = get_postcode_data(postcode_b_final)
            
            if not data_a:
                st.session_state.results = {"error": f"Postcode {postcode_a_final} not found"}
                st.session_state.loading = False
                st.rerun()
            if not data_b:
                st.session_state.results = {"error": f"Postcode {postcode_b_final} not found"}
                st.session_state.loading = False
                st.rerun()

            lat_a, lng_a = data_a["lat"], data_a["lng"]
            lat_b, lng_b = data_b["lat"], data_b["lng"]
            radius_meters = parse_radius_setting(radius)
            polygon_a = make_circle_polygon(lat_a, lng_a, radius_meters)
            polygon_b = make_circle_polygon(lat_b, lng_b, radius_meters)

            events_a, events_b = run_async(fetch_both_postcodes_async(
                {"postcode": postcode_a_final, "lat": lat_a, "lng": lng_a},
                {"postcode": postcode_b_final, "lat": lat_b, "lng": lng_b},
                polygon_a, polygon_b, latest_month))

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
                "postcode_a": postcode_a_final, "postcode_b": postcode_b_final,
                "a": {"total": summary_a["total_crimes"], "violent": summary_a["by_category"].get("violence-and-sexual-offences", 0),
                      "burglary": summary_a["by_category"].get("burglary", 0), "risk_score": summary_a["risk_score"],
                      "risk_level": get_risk_level(summary_a["risk_score"]), "by_category": summary_a["by_category"]},
                "b": {"total": summary_b["total_crimes"], "violent": summary_b["by_category"].get("violence-and-sexual-offences", 0),
                      "burglary": summary_b["by_category"].get("burglary", 0), "risk_score": summary_b["risk_score"],
                      "risk_level": get_risk_level(summary_b["risk_score"]), "by_category": summary_b["by_category"]},
                "quarter": {"months": quarter_months,
                           "a": {"total": quarter_summary_a["total_crimes"], "violent": quarter_summary_a["by_category"].get("violence-and-sexual-offences", 0),
                                "burglary": quarter_summary_a["by_category"].get("burglary", 0), "risk_score": quarter_summary_a["risk_score"],
                                "risk_level": get_risk_level(quarter_summary_a["risk_score"]), "by_category": quarter_summary_a["by_category"]},
                           "b": {"total": quarter_summary_b["total_crimes"], "violent": quarter_summary_b["by_category"].get("violence-and-sexual-offences", 0),
                                "burglary": quarter_summary_b["by_category"].get("burglary", 0), "risk_score": quarter_summary_b["risk_score"],
                                "risk_level": get_risk_level(quarter_summary_b["risk_score"]), "by_category": quarter_summary_b["by_category"]}},
                "month": latest_month, "radius": radius, "radius_meters": radius_meters,
                "coords": {"lat_a": lat_a, "lng_a": lng_a, "lat_b": lat_b, "lng_b": lng_b},
                "monthly": {"months": quarter_months, "a": monthly_summaries_a, "b": monthly_summaries_b}}
        except Exception as e:
            st.session_state.results = {"error": str(e)}

        st.session_state.loading = False
        st.session_state.should_scroll = True  # Trigger auto-scroll to results
        st.rerun()

    elif st.session_state.results is not None:
        data = st.session_state.results
        if "error" in data:
            st.error(f"Error: {data['error']}")
            st.stop()

        st.markdown('<div class="results-header"><h4 class="results-title">Results</h4></div>', unsafe_allow_html=True)

        # Auto-scroll to results
        if st.session_state.should_scroll:
            components.html(
                """
                <script>
                    const anchor = window.parent.document.getElementById('results-anchor');
                    if (anchor) {
                        anchor.scrollIntoView({behavior: 'smooth', block: 'start'});
                    }
                </script>
                """,
                height=0
            )
            st.session_state.should_scroll = False

        tab_month, tab_quarter = st.tabs(["By Months", "Last Quarter"])

        with tab_month:
            month_options = data.get("monthly", {}).get("months", [data["month"]])
            default_month = month_options.index(data["month"]) if data["month"] in month_options else len(month_options) - 1
            st.markdown("<p style='text-align: center; font-weight: 600; color: #64748b; margin-bottom: 0.5rem; font-size: 0.9rem;'>📅 Select Month</p>", unsafe_allow_html=True)
            month_cols = st.columns([2, 1, 2])
            with month_cols[1]:
                selected_month = st.selectbox("Month", month_options, index=default_month, key="month_select", format_func=format_month_label, label_visibility="collapsed")
            
            m_a = data.get("monthly", {}).get("a", {}).get(selected_month, {"total_crimes": 0, "risk_score": 0, "by_category": {}})
            m_b = data.get("monthly", {}).get("b", {}).get(selected_month, {"total_crimes": 0, "risk_score": 0, "by_category": {}})

            # Compact side-by-side comparison cards
            radius_meters = data.get("radius_meters", 805)
            density_a_sel = calc_density(m_a.get("total_crimes", 0), radius_meters)
            density_b_sel = calc_density(m_b.get("total_crimes", 0), radius_meters)
            st.markdown(f'''
            <div class="comparison-row fade-in">
                <div class="comparison-card card-a">
                    <div class="card-header">
                        <span class="dot-a"></span>
                        <span class="postcode-name">{data['postcode_a']}</span>
                    </div>
                    <div class="card-metrics">
                        <div class="metric">
                            <span class="metric-value">{m_a.get("total_crimes", 0)}</span>
                            <span class="metric-label">Crimes</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{m_a.get("risk_score", 0)}</span>
                            <span class="metric-label">Risk Score</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{density_a_sel}</span>
                            <span class="metric-label">Density / km<sup>2</sup></span>
                        </div>
                    </div>
                </div>
                <div class="vs-badge-small">VS</div>
                <div class="comparison-card card-b">
                    <div class="card-header">
                        <span class="dot-b"></span>
                        <span class="postcode-name">{data['postcode_b']}</span>
                    </div>
                    <div class="card-metrics">
                        <div class="metric">
                            <span class="metric-value">{m_b.get("total_crimes", 0)}</span>
                            <span class="metric-label">Crimes</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{m_b.get("risk_score", 0)}</span>
                            <span class="metric-label">Risk Score</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{density_b_sel}</span>
                            <span class="metric-label">Density / km<sup>2</sup></span>
                        </div>
                    </div>
                </div>
            </div>
            ''', unsafe_allow_html=True)
            
            all_categories = set(m_a.get('by_category', {}).keys()) | set(m_b.get('by_category', {}).keys())
            category_data = [{'Category': format_category_name(cat), data['postcode_a']: m_a.get('by_category', {}).get(cat, 0),
                            data['postcode_b']: m_b.get('by_category', {}).get(cat, 0)} for cat in all_categories]
            df_cat = pd.DataFrame(category_data).sort_values(by=data['postcode_a'], ascending=False) if category_data else pd.DataFrame()
            
            fig_cat = go.Figure()
            fig_cat.add_trace(go.Bar(name=data['postcode_a'], x=df_cat['Category'], y=df_cat.get(data['postcode_a'], []),
                                    marker_color='#0d9488', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_cat.add_trace(go.Bar(name=data['postcode_b'], x=df_cat['Category'], y=df_cat.get(data['postcode_b'], []),
                                    marker_color='#3b82f6', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_cat.update_layout(title="Crime Types Side-by-Side", xaxis_title="", yaxis_title="Incidents",
                                barmode='group', height=420, xaxis_tickangle=-45,
                                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            st.plotly_chart(fig_cat, use_container_width=True, key="category_chart", config={"displayModeBar": False})

            trend_categories = set()
            for month_key, summary in data.get('monthly', {}).get('a', {}).items():
                trend_categories.update(summary.get('by_category', {}).keys())
            for month_key, summary in data.get('monthly', {}).get('b', {}).items():
                trend_categories.update(summary.get('by_category', {}).keys())
            
            trend_options = ["All categories"] + sorted(trend_categories)
            trend_labels = {opt: ("All categories" if opt == "All categories" else format_category_name(opt)) for opt in trend_options}
            st.markdown("<p style='text-align: center; font-weight: 600; color: #64748b; margin-bottom: 0.5rem; font-size: 0.9rem;'>📈 Select Category</p>", unsafe_allow_html=True)
            trend_cols = st.columns([2, 1, 2])
            with trend_cols[1]:
                selected_trend = st.selectbox("Trend category", trend_options, format_func=lambda x: trend_labels[x], index=0, label_visibility="collapsed")
            
            trend_rows = []
            for month_key in data.get('monthly', {}).get('months', []):
                summary_a = data['monthly']['a'].get(month_key, {})
                summary_b = data['monthly']['b'].get(month_key, {})
                count_a = summary_a.get('total_crimes', 0) if selected_trend == "All categories" else summary_a.get('by_category', {}).get(selected_trend, 0)
                count_b = summary_b.get('total_crimes', 0) if selected_trend == "All categories" else summary_b.get('by_category', {}).get(selected_trend, 0)
                trend_rows.extend([{"Month": format_month_label(month_key), "Postcode": data['postcode_a'], "Count": count_a},
                                 {"Month": format_month_label(month_key), "Postcode": data['postcode_b'], "Count": count_b}])
            
            trend_df = pd.DataFrame(trend_rows)
            fig_trend = go.Figure()
            fig_trend.add_trace(go.Scatter(x=trend_df[trend_df['Postcode'] == data['postcode_a']]['Month'],
                                          y=trend_df[trend_df['Postcode'] == data['postcode_a']]['Count'],
                                          mode='lines+markers', name=data['postcode_a'],
                                          line=dict(color='#0d9488', width=3), marker=dict(size=10)))
            fig_trend.add_trace(go.Scatter(x=trend_df[trend_df['Postcode'] == data['postcode_b']]['Month'],
                                          y=trend_df[trend_df['Postcode'] == data['postcode_b']]['Count'],
                                          mode='lines+markers', name=data['postcode_b'],
                                          line=dict(color='#3b82f6', width=3), marker=dict(size=10)))
            fig_trend.update_layout(title=f"{trend_labels.get(selected_trend)}", xaxis_title="", yaxis_title="Incidents",
                                  height=420, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                  font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            fig_trend.update_xaxes(type="category", categoryorder="array",
                                  categoryarray=[format_month_label(m) for m in data.get('monthly', {}).get('months', [])])
            st.plotly_chart(fig_trend, use_container_width=True, key="trend_chart", config={"displayModeBar": False})

            
            # Severity mix (High / Medium / Low) for selected month
            sev_a = severity_buckets(m_a.get("by_category", {}))
            sev_b = severity_buckets(m_b.get("by_category", {}))
            fig_sev = go.Figure()
            # Sequential palette based on postcode A color (teal)
            for label, color in [("High", "#1d4ed8"), ("Medium", "#60a5fa"), ("Low", "#bfdbfe")]:
                fig_sev.add_trace(go.Bar(
                    name=label,
                    x=[data['postcode_a'], data['postcode_b']],
                    y=[sev_a.get(label, 0), sev_b.get(label, 0)],
                    marker_color=color,
                    hovertemplate="<b>%{x}</b><br>" + label + ": %{y}<extra></extra>"
                ))
            fig_sev.update_layout(barmode='stack', title="Severity", xaxis_title="", yaxis_title="Incidents",
                                  height=420, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                  font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            st.plotly_chart(fig_sev, use_container_width=True, key="severity_month", config={"displayModeBar": False})

            # Monthly Summary - Enhanced
            total_a_m = m_a.get("total_crimes", 0)
            total_b_m = m_b.get("total_crimes", 0)
            risk_level_a = get_risk_level(m_a.get("risk_score", 0))
            risk_level_b = get_risk_level(m_b.get("risk_score", 0))
            cat_a_m = m_a.get("by_category", {})
            cat_b_m = m_b.get("by_category", {})
            
            # Calculate metrics
            safer_text = calc_safer_text(total_a_m, total_b_m, data['postcode_a'], data['postcode_b'])
            top_cat_a, top_pct_a = calc_top_category(cat_a_m, total_a_m)
            top_cat_b, top_pct_b = calc_top_category(cat_b_m, total_b_m)
            differences = calc_key_differences(cat_a_m, cat_b_m, limit=3)
            diff_html = format_differences_html(differences)
            
            summary_html = f'''<div class="text-summary fade-in">
<h4>Summary — {format_month_label(selected_month)}</h4>
<p class="winner-line">{safer_text}</p>
<div class="summary-grid">
<div class="summary-col summary-col-a">
<div class="summary-postcode">{data['postcode_a']}</div>
<div class="summary-stat"><span class="stat-value">{total_a_m}</span> crimes</div>
<div class="summary-stat">Risk: <span class="stat-value">{m_a.get("risk_score", 0)}</span> <span class="badge {get_risk_badge_class(m_a.get('risk_score', 0))}">{risk_level_a}</span></div>
</div>
<div class="summary-col summary-col-b">
<div class="summary-postcode">{data['postcode_b']}</div>
<div class="summary-stat"><span class="stat-value">{total_b_m}</span> crimes</div>
<div class="summary-stat">Risk: <span class="stat-value">{m_b.get("risk_score", 0)}</span> <span class="badge {get_risk_badge_class(m_b.get('risk_score', 0))}">{risk_level_b}</span></div>
</div>
</div>
<div class="summary-section">
<strong>Key differences:</strong><br>{diff_html}
</div>
<div class="summary-section">
<strong>Top concern:</strong><br>• {data['postcode_a']}: {top_cat_a} ({top_pct_a}%)<br>• {data['postcode_b']}: {top_cat_b} ({top_pct_b}%)
</div>
<p class="summary-footer"><em>Data within {data['radius']} of each postcode.</em></p>
</div>'''
            st.markdown(summary_html, unsafe_allow_html=True)

        with tab_quarter:
            st.caption(f"Search area: {data['radius']}")
            st.caption(f"Covering {format_month_range(data['quarter']['months'])}")

            # Quarter comparison cards (Crimes / Risk / Density)
            radius_meters_q = data.get("radius_meters", 805)
            q_a = data['quarter']['a']
            q_b = data['quarter']['b']
            density_a_q = calc_density(q_a["total"], radius_meters_q)
            density_b_q = calc_density(q_b["total"], radius_meters_q)
            st.markdown(f'''
            <div class="comparison-row fade-in">
                <div class="comparison-card card-a">
                    <div class="card-header">
                        <span class="dot-a"></span>
                        <span class="postcode-name">{data['postcode_a']}</span>
                    </div>
                    <div class="card-metrics">
                        <div class="metric">
                            <span class="metric-value">{q_a["total"]}</span>
                            <span class="metric-label">Crimes</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{q_a["risk_score"]}</span>
                            <span class="metric-label">Risk Score</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{density_a_q}</span>
                            <span class="metric-label">Density / km<sup>2</sup></span>
                        </div>
                    </div>
                </div>
                <div class="vs-badge-small">VS</div>
                <div class="comparison-card card-b">
                    <div class="card-header">
                        <span class="dot-b"></span>
                        <span class="postcode-name">{data['postcode_b']}</span>
                    </div>
                    <div class="card-metrics">
                        <div class="metric">
                            <span class="metric-value">{q_b["total"]}</span>
                            <span class="metric-label">Crimes</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{q_b["risk_score"]}</span>
                            <span class="metric-label">Risk Score</span>
                        </div>
                        <div class="metric-divider"></div>
                        <div class="metric">
                            <span class="metric-value">{density_b_q}</span>
                            <span class="metric-label">Density / km<sup>2</sup></span>
                        </div>
                    </div>
                </div>
            </div>
            ''', unsafe_allow_html=True)
            
            quarter_categories = set(data['quarter']['a']['by_category'].keys()) | set(data['quarter']['b']['by_category'].keys())
            quarter_category_data = [{'Category': format_category_name(cat), data['postcode_a']: data['quarter']['a']['by_category'].get(cat, 0),
                                     data['postcode_b']: data['quarter']['b']['by_category'].get(cat, 0)} for cat in quarter_categories]
            df_q_cat = pd.DataFrame(quarter_category_data).sort_values(by=data['postcode_a'], ascending=False) if quarter_category_data else pd.DataFrame()
            
            fig_q_cat = go.Figure()
            fig_q_cat.add_trace(go.Bar(name=data['postcode_a'], x=df_q_cat['Category'], y=df_q_cat[data['postcode_a']],
                                      marker_color='#0d9488', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_q_cat.add_trace(go.Bar(name=data['postcode_b'], x=df_q_cat['Category'], y=df_q_cat[data['postcode_b']],
                                      marker_color='#3b82f6', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_q_cat.update_layout(title="Crime Types Side-by-Side", xaxis_title="", yaxis_title="Incidents",
                                  barmode='group', height=460, xaxis_tickangle=-45,
                                  paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                  font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            st.plotly_chart(fig_q_cat, use_container_width=True, key="quarter_category_chart", config={"displayModeBar": False})

            # Grouped structure (quarter)
            grouped_a = group_categories(data['quarter']['a']['by_category'])
            grouped_b = group_categories(data['quarter']['b']['by_category'])
            grouped_df = pd.DataFrame({
                "Group": list(grouped_a.keys()),
                data['postcode_a']: list(grouped_a.values()),
                data['postcode_b']: list(grouped_b.values())
            })
            fig_group = go.Figure()
            fig_group.add_trace(go.Bar(name=data['postcode_a'], x=grouped_df["Group"], y=grouped_df[data['postcode_a']],
                                       marker_color='#0d9488', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_group.add_trace(go.Bar(name=data['postcode_b'], x=grouped_df["Group"], y=grouped_df[data['postcode_b']],
                                       marker_color='#3b82f6', hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'))
            fig_group.update_layout(title="Crime profile (grouped categories)", xaxis_title="", yaxis_title="Incidents",
                                    barmode='group', height=420,
                                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                    font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            st.plotly_chart(fig_group, use_container_width=True, key="grouped_quarter", config={"displayModeBar": False})

            # Severity mix
            sev_q_a = severity_buckets(data['quarter']['a']['by_category'])
            sev_q_b = severity_buckets(data['quarter']['b']['by_category'])
            fig_sev_q = go.Figure()
            for label, color in [("High", "#1d4ed8"), ("Medium", "#60a5fa"), ("Low", "#bfdbfe")]:
                fig_sev_q.add_trace(go.Bar(
                    name=label,
                    x=[data['postcode_a'], data['postcode_b']],
                    y=[sev_q_a.get(label, 0), sev_q_b.get(label, 0)],
                    marker_color=color,
                    hovertemplate="<b>%{x}</b><br>" + label + ": %{y}<extra></extra>"
                ))
            fig_sev_q.update_layout(barmode='stack', title="Severity", xaxis_title="", yaxis_title="Incidents",
                                    height=420, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                    font=dict(family='Plus Jakarta Sans, DM Sans, sans-serif', size=12))
            st.plotly_chart(fig_sev_q, use_container_width=True, key="severity_quarter", config={"displayModeBar": False})

            # Quarter Summary - Enhanced with Trend
            total_a_q = q_a['total']
            total_b_q = q_b['total']
            cat_a_q = q_a.get('by_category', {})
            cat_b_q = q_b.get('by_category', {})
            quarter_months = data['quarter']['months']
            
            # Calculate metrics
            safer_text_q = calc_safer_text(total_a_q, total_b_q, data['postcode_a'], data['postcode_b'])
            top_cat_a_q, top_pct_a_q = calc_top_category(cat_a_q, total_a_q)
            top_cat_b_q, top_pct_b_q = calc_top_category(cat_b_q, total_b_q)
            differences_q = calc_key_differences(cat_a_q, cat_b_q, limit=3)
            diff_html_q = format_differences_html(differences_q)
            
            # Calculate trends
            monthly_a = data.get('monthly', {}).get('a', {})
            monthly_b = data.get('monthly', {}).get('b', {})
            trend_pct_a, trend_text_a = calc_trend(monthly_a, quarter_months)
            trend_pct_b, trend_text_b = calc_trend(monthly_b, quarter_months)
            monthly_vals_a = get_monthly_values(monthly_a, quarter_months)
            monthly_vals_b = get_monthly_values(monthly_b, quarter_months)
            
            # Monthly average
            avg_a = round(total_a_q / len(quarter_months)) if quarter_months else 0
            avg_b = round(total_b_q / len(quarter_months)) if quarter_months else 0
            
            summary_html_q = f'''<div class="text-summary fade-in">
<h4>Summary — {format_month_range(quarter_months)}</h4>
<p class="winner-line">{safer_text_q}</p>
<div class="summary-grid">
<div class="summary-col summary-col-a">
<div class="summary-postcode">{data['postcode_a']}</div>
<div class="summary-stat"><span class="stat-value">{total_a_q}</span> crimes (~{avg_a}/month)</div>
<div class="summary-stat">Risk: <span class="stat-value">{q_a['risk_score']}</span> <span class="badge {get_risk_badge_class(q_a['risk_score'])}">{q_a['risk_level']}</span></div>
<div class="summary-stat">Trend: <span class="stat-value">{trend_text_a}</span></div>
</div>
<div class="summary-col summary-col-b">
<div class="summary-postcode">{data['postcode_b']}</div>
<div class="summary-stat"><span class="stat-value">{total_b_q}</span> crimes (~{avg_b}/month)</div>
<div class="summary-stat">Risk: <span class="stat-value">{q_b['risk_score']}</span> <span class="badge {get_risk_badge_class(q_b['risk_score'])}">{q_b['risk_level']}</span></div>
<div class="summary-stat">Trend: <span class="stat-value">{trend_text_b}</span></div>
</div>
</div>
<div class="summary-section">
<strong>Key differences:</strong><br>{diff_html_q}
</div>
<div class="summary-section">
<strong>Top concern:</strong><br>• {data['postcode_a']}: {top_cat_a_q} ({top_pct_a_q}%)<br>• {data['postcode_b']}: {top_cat_b_q} ({top_pct_b_q}%)
</div>
<div class="summary-section">
<strong>3-month trend:</strong><br>• {data['postcode_a']}: {monthly_vals_a} ({trend_text_a})<br>• {data['postcode_b']}: {monthly_vals_b} ({trend_text_b})
</div>
<p class="summary-footer"><em>Data within {data['radius']} of each postcode.</em></p>
</div>'''
            st.markdown(summary_html_q, unsafe_allow_html=True)

        # ======================== INTERACTIVE MAP ========================
        if 'coords' in data:
            coords = data['coords']
            comparison_map = create_comparison_map(
                coords['lat_a'], coords['lng_a'],
                coords['lat_b'], coords['lng_b'],
                data['postcode_a'], data['postcode_b'],
                data.get('radius_meters', 805)
            )
            
            st_folium(comparison_map, width=None, height=400, returned_objects=[])
            
            st.markdown(f'''
            <div class="map-legend">
                <div class="map-legend-item">
                    <span class="map-legend-dot a"></span>
                    {data['postcode_a']}
                </div>
                <div class="map-legend-item">
                    <span class="map-legend-dot b"></span>
                    {data['postcode_b']}
                </div>
            </div>
            ''', unsafe_allow_html=True)

# ======================== FEEDBACK ========================
with st.expander("💬 Share Your Feedback", expanded=False):
    with st.form("feedback_form"):
        top_cols = st.columns([1.5, 4])
        with top_cols[0]:
            st.markdown("<p style='font-weight: 600; margin-bottom: 0.5rem; color: #334155;'>Was this helpful?</p>", unsafe_allow_html=True)
            rating_choice = st.radio("Rating", ["👍 Yes", "👎 No"], horizontal=True, index=None, label_visibility="collapsed")
            rating = 5 if rating_choice and "👍" in rating_choice else (1 if rating_choice else None)
        with top_cols[1]:
            st.markdown("<p style='font-weight: 600; margin-bottom: 0.5rem; color: #334155;'>Your thoughts (optional)</p>", unsafe_allow_html=True)
            feedback_text = st.text_area("Comments", placeholder="What could we improve? Any bugs to report?", height=100, max_chars=300, label_visibility="collapsed")
        
        btn_cols = st.columns([3, 2, 3])
        with btn_cols[1]:
            submitted = st.form_submit_button("Send Feedback", use_container_width=True)
        
        if submitted:
            if rating is None and not feedback_text:
                st.warning("Please select Yes/No or write a comment")
            else:
                try:
                    save_feedback(user_id, rating or 0, feedback_text or "", "crime_comparator")
                    st.success("Thank you for your feedback!")
                except Exception as e:
                    st.error(f"Failed: {e}")

# ======================== FOOTER ========================
st.markdown('''
<div class="footer-minimal">
    Contains public sector information licensed under the 
    <a href="http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" target="_blank">Open Government Licence v3.0</a>.
    <br>
    Data Sources: 
    <a href="https://data.police.uk/" target="_blank">Police.uk API</a> | 
    <a href="https://geoportal.statistics.gov.uk/" target="_blank">ONS Postcode Directory</a>
    <br>
    CrimeCompare © 2025
</div>
''', unsafe_allow_html=True)
