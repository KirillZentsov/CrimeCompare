import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
import math

from config import settings
from supabase_utils import init_supabase, get_supabase_client, supabase_available
from fingerprint_utils import get_user_id
from quota_manager import check_limit, increment_usage
from feedback_manager import save_feedback
from llm_utils import summarize_crime_comparison_llm
from crime_api import fetch_crimes_polygon, summarize_crimes, get_top_crime_categories, format_category_name
from token_manager import get_settings_row

# ======================== PAGE CONFIG ========================
st.set_page_config(
    page_title="CrimeCompare England | Crime Data Analysis",
    page_icon="🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ======================== CUSTOM CSS ========================
st.markdown("""
<style>
    /* Main container width optimization */
    .main .block-container {
        max-width: 1400px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }
    
    /* Header styling */
    .main-header {
        text-align: center;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem 1rem;
        border-radius: 15px;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    .main-title {
        font-size: 3rem;
        font-weight: 700;
        margin: 0;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
    }
    
    .slogan {
        font-size: 1.2rem;
        margin-top: 0.5rem;
        opacity: 0.95;
        font-weight: 300;
    }
    
    .description {
        font-size: 0.95rem;
        margin-top: 1rem;
        line-height: 1.6;
        max-width: 800px;
        margin-left: auto;
        margin-right: auto;
        opacity: 0.9;
    }
    
    /* Postcode input cards */
    .postcode-card {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        padding: 1.5rem;
        border-radius: 12px;
        border: 2px solid #e1e8ed;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        height: 100%;
    }
    
    /* Comparison result cards */
    .result-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        border-left: 4px solid #667eea;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    
    /* Risk badge styling */
    .risk-low {
        background: #d4edda;
        color: #155724;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-weight: 600;
        display: inline-block;
    }
    
    .risk-medium {
        background: #fff3cd;
        color: #856404;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-weight: 600;
        display: inline-block;
    }
    
    .risk-high {
        background: #f8d7da;
        color: #721c24;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-weight: 600;
        display: inline-block;
    }
    
    /* Settings panel */
    .settings-panel {
        background: #f8f9fa;
        padding: 1.5rem;
        border-radius: 10px;
        border: 1px solid #dee2e6;
        margin-bottom: 2rem;
    }
    
    /* Footer attribution */
    .attribution {
        background: #2c3e50;
        color: #ecf0f1;
        padding: 1.5rem;
        border-radius: 8px;
        margin-top: 3rem;
        font-size: 0.85rem;
        line-height: 1.6;
    }
    
    .attribution a {
        color: #3498db;
        text-decoration: none;
    }
    
    .attribution a:hover {
        text-decoration: underline;
    }
    
    /* Metric cards */
    div[data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
    }
    
    /* Radio buttons spacing */
    div[role="radiogroup"] {
        gap: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# ======================== HELPER FUNCTIONS ========================
def make_circle_polygon(lat, lng, radius_m, num_points=32):
    """Create circular polygon coordinates for given center and radius."""
    pts = []
    R = 6371000  # Earth radius in meters
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        d_lat = (radius_m / R) * math.cos(angle)
        d_lng = (radius_m / (R * math.cos(math.radians(lat)))) * math.sin(angle)
        new_lat = lat + math.degrees(d_lat)
        new_lng = lng + math.degrees(d_lng)
        pts.append([new_lat, new_lng])
    return pts

def get_risk_badge_html(risk_score):
    """Generate HTML for risk level badge."""
    if risk_score < 40:
        cls = "risk-low"
        label = "Low Risk"
        emoji = "🟢"
    elif risk_score < 70:
        cls = "risk-medium"
        label = "Medium Risk"
        emoji = "🟡"
    else:
        cls = "risk-high"
        label = "High Risk"
        emoji = "🔴"
    
    return f'<span class="{cls}">{emoji} {label} ({risk_score})</span>'

# ======================== INITIALIZATION ========================
if "supabase_initialized" not in st.session_state:
    init_supabase()
    st.session_state["supabase_initialized"] = True

if not supabase_available():
    st.error("⚠️ Database connection failed. Please check your configuration.")
    st.stop()

# ======================== ADMIN MODE CHECK ========================
query_params = st.query_params
admin_key_env = os.getenv("ADMIN_KEY")
is_admin = ("admin" in query_params and admin_key_env 
            and query_params["admin"] == admin_key_env)

if is_admin:
    st.title("🛠 Admin Panel - CrimeCompare England")
    
    try:
        settings_row = get_settings_row()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Service Status", "⛔ Paused" if settings_row["service_paused"] else "✅ Active")
        col2.metric("Monthly Budget", f"${settings_row['monthly_budget_limit']}")
        col3.metric("Total Spent", f"${round(settings_row['total_spent'], 4)}")
        
        st.markdown("---")
        
        col_a, col_b = st.columns(2)
        
        with col_a:
            if st.button("🔄 Toggle Service Status", use_container_width=True):
                supabase = get_supabase_client()
                new_val = not settings_row["service_paused"]
                supabase.table("settings").update({"service_paused": new_val}).eq("id", 1).execute()
                st.success(f"Service {'paused' if new_val else 'resumed'}")
                st.rerun()
        
        with col_b:
            if st.button("💰 Reset Spending", use_container_width=True):
                supabase = get_supabase_client()
                supabase.table("settings").update({"total_spent": 0}).eq("id", 1).execute()
                st.success("Total spending reset to $0")
                st.rerun()
        
        st.markdown("---")
        st.subheader("📊 Today's Usage")
        
        today = date.today().isoformat()
        supabase = get_supabase_client()
        ul = supabase.table("usage_limits").select("*").eq("date", today).execute()
        
        if ul.data:
            df_ul = pd.DataFrame(ul.data)
            st.dataframe(df_ul, use_container_width=True)
        else:
            st.info("No usage recorded today.")
            
    except Exception as e:
        st.error(f"Admin panel error: {e}")
    
    st.stop()

# ======================== MAIN APP ========================
# Header
st.markdown("""
<div class="main-header">
    <h1 class="main-title">🏴󠁧󠁢󠁥󠁮󠁧󠁿 CrimeCompare England</h1>
    <p class="slogan">Crime data. Postcode clarity.</p>
    <p class="description">
        The system collects official police data, breaks it down by crime type and frequency, 
        and presents clear comparisons to help you understand the safety profile of any area.
    </p>
</div>
""", unsafe_allow_html=True)

# User ID and checks
user_id = get_user_id()

try:
    settings_row = get_settings_row()
except Exception as e:
    st.error(f"⚠️ Failed to load settings: {e}")
    st.stop()

if settings_row["service_paused"]:
    st.error("⛔ Service is temporarily paused for maintenance. Please try again later.")
    st.stop()

try:
    if check_limit(user_id):
        st.warning("⏳ You've reached your daily limit. Please try again tomorrow.")
        st.stop()
except Exception as e:
    st.error(f"Failed to check quota: {e}")
    st.stop()

# ======================== INPUT SECTION ========================
st.markdown("## 📍 Enter Two Postcodes")

col1, col2 = st.columns(2)

with col1:
    st.markdown('<div class="postcode-card">', unsafe_allow_html=True)
    st.markdown("### 📮 Postcode A")
    postcode_a = st.text_input(
        "Enter first postcode",
        placeholder="e.g., SW1A 1AA",
        key="postcode_a",
        label_visibility="collapsed"
    )
    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    st.markdown('<div class="postcode-card">', unsafe_allow_html=True)
    st.markdown("### 📮 Postcode B")
    postcode_b = st.text_input(
        "Enter second postcode",
        placeholder="e.g., E1 6AN",
        key="postcode_b",
        label_visibility="collapsed"
    )
    st.markdown('</div>', unsafe_allow_html=True)

# ======================== SETTINGS SECTION ========================
st.markdown('<div class="settings-panel">', unsafe_allow_html=True)
st.markdown("## ⚙️ Comparison Settings")

# Month Selection
available_months = [
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05",
    "2025-06", "2025-07", "2025-08", "2025-09"
]

col_m1, col_m2 = st.columns([3, 1])

with col_m1:
    selected_month = st.selectbox(
        "📅 Select month for crime comparison",
        available_months,
        index=0
    )

with col_m2:
    st.markdown(
        """
        <div style="
            background:#e8f4f8;
            padding:12px;
            margin-top:28px;
            border-radius:8px;
            text-align:center;
            border:2px solid #b8dce8;
        ">
            <small>Latest available</small><br>
            <strong style="font-size:1.1rem;">2025-09</strong>
        </div>
        """,
        unsafe_allow_html=True
    )

st.markdown("---")

# Area Selection
st.markdown("### 🗺️ Area Selection Method")

mode = st.radio(
    "Choose how to define the search area:",
    options=["By radius", "By walking time"],
    horizontal=True,
    help="Radius uses direct distance, walking time estimates distance covered at normal walking pace"
)

distance_meters = None

if mode == "By radius":
    radius_miles = st.radio(
        "Search radius:",
        options=[0.5, 1.0],
        format_func=lambda x: f"{x} mile" if x == 1.0 else f"{x} miles",
        horizontal=True
    )
    distance_meters = radius_miles * 1609.34
    st.info(f"🔍 Effective search radius: **{int(distance_meters)} meters** ({radius_miles} mile{'s' if radius_miles != 1 else ''})")
else:
    walking_minutes = st.radio(
        "Walking time:",
        options=[5, 10],
        format_func=lambda x: f"{x} minutes",
        horizontal=True
    )
    WALK_SPEED_M_PER_MIN = 80  # Average adult walking speed
    distance_meters = walking_minutes * WALK_SPEED_M_PER_MIN
    st.info(f"🚶 Estimated walking radius: **{int(distance_meters)} meters** ({walking_minutes} minute walk)")

st.markdown('</div>', unsafe_allow_html=True)

# ======================== COMPARE BUTTON ========================
if st.button("🔍 Compare Crime Data", type="primary", use_container_width=True):
    # Validate inputs
    if not postcode_a or not postcode_b:
        st.error("❌ Please enter both postcodes")
        st.stop()
    
    postcode_a = postcode_a.strip().upper()
    postcode_b = postcode_b.strip().upper()
    
    if postcode_a == postcode_b:
        st.error("❌ Please enter two different postcodes")
        st.stop()
    
    # Increment usage
    try:
        increment_usage(user_id)
    except Exception as e:
        st.warning(f"Could not update usage counter: {e}")
    
    # Fetch data for both postcodes
    supabase = get_supabase_client()
    results = []
    
    with st.spinner("🔄 Fetching crime data..."):
        for pc in [postcode_a, postcode_b]:
            try:
                # Get postcode coordinates
                res = supabase.table("postcodes").select("*").eq("postcode", pc).maybe_single().execute()
                row = res.data
                
                if not row:
                    results.append({"postcode": pc, "error": "Postcode not found in database"})
                    continue
                
                lat, lng = row["lat"], row["lng"]
                
                # Create polygon
                polygon = make_circle_polygon(lat, lng, distance_meters)
                
                # Fetch crimes
                events = fetch_crimes_polygon(polygon, selected_month)
                summary = summarize_crimes(events)
                
                results.append({
                    "postcode": pc,
                    "lat": lat,
                    "lng": lng,
                    "total_crimes": summary["total_crimes"],
                    "risk_score": summary["risk_score"],
                    "by_category": summary["by_category"],
                    "events": events
                })
                
            except Exception as e:
                results.append({"postcode": pc, "error": str(e)})
    
    # ======================== DISPLAY RESULTS ========================
    st.markdown("---")
    st.markdown("## 📊 Comparison Results")
    
    # Check for errors
    has_error = any("error" in r for r in results)
    if has_error:
        for r in results:
            if "error" in r:
                st.error(f"**{r['postcode']}**: {r['error']}")
        st.stop()
    
    # Display overview cards
    col1, col2 = st.columns(2)
    
    for idx, r in enumerate(results):
        with [col1, col2][idx]:
            st.markdown(f"""
            <div class="result-card">
                <h2 style="margin:0; color:#667eea;">📮 {r['postcode']}</h2>
                <p style="color:#666; font-size:0.9rem; margin:0.5rem 0;">
                    {r['lat']:.4f}, {r['lng']:.4f}
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            st.metric("Total Crimes", r['total_crimes'])
            st.markdown(get_risk_badge_html(r['risk_score']), unsafe_allow_html=True)
            
            if r['by_category']:
                with st.expander("📋 Crime Breakdown"):
                    for cat, count in sorted(r['by_category'].items(), key=lambda x: x[1], reverse=True):
                        st.write(f"**{format_category_name(cat)}**: {count}")
    
    # ======================== COMPARISON CHARTS ========================
    st.markdown("---")
    st.markdown("## 📈 Visual Comparison")
    
    # Total crimes comparison
    df_total = pd.DataFrame(results)
    
    fig_total = go.Figure(data=[
        go.Bar(
            x=df_total['postcode'],
            y=df_total['total_crimes'],
            marker_color=['#667eea', '#764ba2'],
            text=df_total['total_crimes'],
            textposition='auto',
        )
    ])
    
    fig_total.update_layout(
        title="Total Crimes Comparison",
        xaxis_title="Postcode",
        yaxis_title="Number of Crimes",
        height=400,
        showlegend=False
    )
    
    st.plotly_chart(fig_total, use_container_width=True)
    
    # Risk score comparison
    fig_risk = go.Figure(data=[
        go.Bar(
            x=df_total['postcode'],
            y=df_total['risk_score'],
            marker_color=['#3498db', '#e74c3c'],
            text=df_total['risk_score'].round(1),
            textposition='auto',
        )
    ])
    
    fig_risk.update_layout(
        title="Risk Score Comparison (0-100)",
        xaxis_title="Postcode",
        yaxis_title="Risk Score",
        height=400,
        showlegend=False
    )
    
    st.plotly_chart(fig_risk, use_container_width=True)
    
    # Category comparison
    st.markdown("### 🏷️ Crime Categories Comparison")
    
    # Combine all categories
    all_categories = set()
    for r in results:
        all_categories.update(r['by_category'].keys())
    
    category_data = []
    for cat in all_categories:
        category_data.append({
            'Category': format_category_name(cat),
            postcode_a: results[0]['by_category'].get(cat, 0),
            postcode_b: results[1]['by_category'].get(cat, 0)
        })
    
    df_cat = pd.DataFrame(category_data).sort_values(by=postcode_a, ascending=False)
    
    fig_cat = go.Figure()
    
    fig_cat.add_trace(go.Bar(
        name=postcode_a,
        x=df_cat['Category'],
        y=df_cat[postcode_a],
        marker_color='#667eea'
    ))
    
    fig_cat.add_trace(go.Bar(
        name=postcode_b,
        x=df_cat['Category'],
        y=df_cat[postcode_b],
        marker_color='#764ba2'
    ))
    
    fig_cat.update_layout(
        title="Crime Types Side-by-Side",
        xaxis_title="Crime Category",
        yaxis_title="Number of Incidents",
        barmode='group',
        height=500,
        xaxis_tickangle=-45
    )
    
    st.plotly_chart(fig_cat, use_container_width=True)
    
    # ======================== AI SUMMARY ========================
    st.markdown("---")
    st.markdown("## 🤖 AI Analysis")
    
    with st.spinner("✨ Generating intelligent summary..."):
        try:
            ai_summary = summarize_crime_comparison_llm(user_id, results, selected_month)
            st.info(ai_summary)
        except Exception as e:
            st.error(f"Failed to generate AI summary: {e}")

# ======================== FEEDBACK SECTION ========================
st.markdown("---")
st.markdown("## 💬 Your Feedback Matters")

with st.form("feedback_form"):
    col_f1, col_f2 = st.columns([1, 2])
    
    with col_f1:
        rating = st.slider("Rating", 1, 5, 4, help="1 = Poor, 5 = Excellent")
    
    with col_f2:
        feedback_text = st.text_area(
            "Comments (optional)",
            placeholder="Share your thoughts on CrimeCompare England...",
            height=100
        )
    
    submitted = st.form_submit_button("📤 Submit Feedback", use_container_width=True)
    
    if submitted:
        try:
            save_feedback(user_id, rating, feedback_text or "", "crime_comparator")
            st.success("✅ Thank you for your feedback!")
        except Exception as e:
            st.error(f"Failed to save feedback: {e}")

# ======================== ATTRIBUTION FOOTER ========================
st.markdown("""
<div class="attribution">
    <strong>📜 Legal & Data Attribution</strong><br><br>
    
    This service contains public sector information licensed under the 
    <a href="http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" target="_blank">
    Open Government Licence v3.0</a>.<br><br>
    
    <strong>Data Sources:</strong><br>
    • Crime Data: <a href="https://data.police.uk/" target="_blank">Police.uk API</a> 
    (Official UK Police crime statistics)<br>
    • Postcode Data: <a href="https://geoportal.statistics.gov.uk/" target="_blank">
    ONS Postcode Directory</a> (Office for National Statistics)<br><br>
    
    <strong>Disclaimer:</strong> Crime data is provided for informational purposes only. 
    Past crime statistics do not guarantee future safety. Always conduct thorough research 
    and consult local authorities when making important decisions about location and safety.<br><br>
    
    <small>CrimeCompare England © 2025 | User ID: <code>{user_id[:12]}...</code></small>
</div>
""".format(user_id=user_id), unsafe_allow_html=True)
