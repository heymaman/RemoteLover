# dashboard.py – FINAL with Auto‑Refresh
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime, timedelta
import subprocess
import json
import os
import time

st.set_page_config(page_title="Job Hunter Pro", page_icon="🎯", layout="wide")

DB_PATH = "data/jobs.db"
HEALTH_PATH = "data/health.json"

# ─── DARK MODE ───
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
dark = st.sidebar.toggle("🌙 Dark Mode", value=st.session_state.dark_mode)
st.session_state.dark_mode = dark
if dark:
    st.markdown("""
        <style>
        .stApp { background-color: #0e1117; color: white; }
        .stSidebar { background-color: #1a1e27; }
        .stDataFrame { background-color: #1a1e27; }
        .st-bb { background-color: #262730; }
        </style>
    """, unsafe_allow_html=True)

st.title("🎯 Remote Opportunity Hunter")
st.caption("Kanban · Presets · Health · Auto-Refresh")

# ─── AUTO‑REFRESH ───
auto_refresh = st.sidebar.checkbox("🔄 Auto-refresh every 5 minutes", value=False)
if auto_refresh:
    st.sidebar.info("Auto-refresh enabled – page will reload every 5 minutes")
    # We'll use a meta refresh via JavaScript or just rerun on timer.
    # Streamlit doesn't have a built-in timer; we can use st.empty and a sleep loop.
    # For simplicity, we'll use a placeholder and rerun.

# ─── DB HELPERS ───
def table_exists(conn, table_name):
    c = conn.cursor()
    c.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
    return c.fetchone() is not None

def migrate_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA table_info(jobs)")
    cols = [row[1] for row in c.fetchall()]
    if "notes" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN notes TEXT DEFAULT ''")
    if "type" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN type TEXT DEFAULT 'job'")
    conn.commit()
    conn.close()

# ─── LOAD DATA ───
@st.cache_data(ttl=60)
def load_all_data():
    conn = sqlite3.connect(DB_PATH)
    if not table_exists(conn, "jobs"):
        conn.close()
        return pd.DataFrame(columns=['id', 'title', 'company', 'location', 'url', 'source',
                                     'score', 'status', 'notes', 'posted_at', 'seen_at', 'salary', 'type'])
    df = pd.read_sql_query("""
        SELECT id, title, company, location, url, source,
               score, status, notes, posted_at, seen_at, salary, type
        FROM jobs
        ORDER BY seen_at DESC
    """, conn)
    conn.close()
    df['posted_at'] = pd.to_datetime(df['posted_at'], errors='coerce')
    df['seen_at'] = pd.to_datetime(df['seen_at'], errors='coerce')
    return df

# ─── HEALTH ───
def load_health():
    if os.path.exists(HEALTH_PATH):
        with open(HEALTH_PATH) as f:
            return json.load(f)
    return None

# ─── PRESETS ───
def load_presets():
    if "presets" not in st.session_state:
        st.session_state.presets = {
            "All Jobs": {"status": [], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["job", "task"]},
            "🚀 Top 50": {"status": [], "min_score": 70, "sources": [], "search": "", "date_range": "30", "type": ["job", "task"]},
            "🆕 New": {"status": ["new"], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["job", "task"]},
            "✅ Applied": {"status": ["applied"], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["job", "task"]},
            "🎯 Interview": {"status": ["interview"], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["job", "task"]},
            "💼 Jobs Only": {"status": [], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["job"]},
            "🧩 Tasks Only": {"status": [], "min_score": 0, "sources": [], "search": "", "date_range": "30", "type": ["task"]},
        }
    return st.session_state.presets

def save_preset(name, filters):
    st.session_state.presets[name] = filters
    st.success(f"Preset '{name}' saved!")

# ─── SIDEBAR ───
st.sidebar.header("🔍 Filters & Presets")

presets = load_presets()
preset_names = list(presets.keys())
selected_preset = st.sidebar.selectbox("📌 Load Preset", ["None"] + preset_names, index=0)

filters = {}
if selected_preset != "None":
    filters = presets.get(selected_preset, {})
else:
    filters["search"] = st.sidebar.text_input("🔎 Search", value="")
    all_statuses = ["new", "viewed", "applied", "interview", "offer", "rejected"]
    default_status = ["new", "viewed", "applied"]
    filters["status"] = st.sidebar.multiselect("📌 Status", all_statuses, default=default_status)
    filters["min_score"] = st.sidebar.slider("⭐ Min Score", 0, 100, 0, step=5)
    filters["sources"] = []
    conn = sqlite3.connect(DB_PATH)
    if table_exists(conn, "jobs"):
        all_sources = [row[0] for row in conn.execute("SELECT DISTINCT source FROM jobs").fetchall()]
    else:
        all_sources = []
    conn.close()
    filters["sources"] = st.sidebar.multiselect("📡 Source", all_sources, default=all_sources)
    filters["date_range"] = st.sidebar.selectbox("📅 Date Range", ["7", "30", "90", "All"], index=1)
    filters["type"] = st.sidebar.multiselect("📂 Type", ["job", "task"], default=["job", "task"])

if st.sidebar.button("💾 Save Current Filters as Preset"):
    preset_name = st.sidebar.text_input("Preset Name", key="preset_name_input")
    if preset_name:
        save_preset(preset_name, filters)
        st.rerun()

# ─── LOAD DATA ───
migrate_db()
df = load_all_data()

if df.empty:
    st.warning("🚫 No jobs found – database is empty.")
    if st.button("🚀 Run Scraper Now"):
        with st.spinner("Running job hunter..."):
            result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
            if result.returncode == 0:
                st.success("✅ Scraper finished!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ Failed: {result.stderr}")
    st.stop()

# ─── APPLY FILTERS ───
status_filter = filters.get("status", [])
if not status_filter:
    status_filter = df['status'].unique().tolist()
min_score = filters.get("min_score", 0)
source_filter = filters.get("sources", [])
if not source_filter:
    source_filter = df['source'].unique().tolist()
type_filter = filters.get("type", ["job", "task"])
date_range = filters.get("date_range", "30")
if date_range != "All":
    cutoff = datetime.now() - timedelta(days=int(date_range))
    df_filtered = df[(df['seen_at'] >= cutoff)]
else:
    df_filtered = df

filtered = df_filtered[
    (df_filtered['status'].isin(status_filter)) &
    (df_filtered['score'] >= min_score) &
    (df_filtered['source'].isin(source_filter)) &
    (df_filtered['type'].isin(type_filter))
]
if filters.get("search"):
    search = filters["search"].lower()
    filtered = filtered[
        filtered['title'].str.lower().str.contains(search, na=False) |
        filtered['company'].str.lower().str.contains(search, na=False)
    ]

# ─── METRICS ───
total = len(df)
new = len(df[df['status']=='new'])
applied = len(df[df['status']=='applied'])
interview = len(df[df['status']=='interview'])
avg_score = df['score'].mean() if not df.empty else 0

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("📊 Total Jobs", total)
col2.metric("🆕 New", new)
col3.metric("✅ Applied", applied)
col4.metric("🎯 Interviewing", interview)
col5.metric("🏆 Avg Score", f"{avg_score:.1f}")

# ─── HEALTH ───
health = load_health()
if health:
    last_run = datetime.fromisoformat(health["last_run"])
    hours_ago = (datetime.now() - last_run).total_seconds() / 3600
    if hours_ago > 24:
        st.warning(f"⚠️ Scraper hasn't run in {hours_ago:.1f} hours. Last run: {last_run.strftime('%Y-%m-%d %H:%M')}")
    else:
        st.info(f"✅ Scraper ran {hours_ago:.1f} hours ago. Found {health.get('total_matched', 0)} new jobs.")
else:
    st.info("ℹ️ No health data – scraper hasn't run yet.")

# ─── KANBAN ───
st.subheader(f"📋 Kanban Board ({len(filtered)} jobs)")
status_order = ["new", "viewed", "applied", "interview", "offer", "rejected"]
cols = st.columns(len(status_order))

def update_status(job_id, new_status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
    conn.commit()
    conn.close()

for i, status in enumerate(status_order):
    with cols[i]:
        st.markdown(f"**{status.upper()}** ({len(filtered[filtered['status']==status])})")
        jobs_in_col = filtered[filtered['status']==status].head(10)
        for _, row in jobs_in_col.iterrows():
            with st.container():
                st.markdown(f"""
                <div style="background-color: #262730; padding: 10px; border-radius: 5px; margin-bottom: 5px;">
                    <b>{row['company']}</b><br>
                    {row['title']}<br>
                    Score: {row['score']}/100
                </div>
                """, unsafe_allow_html=True)
                new_status = st.selectbox(
                    f"Move {row['id'][:6]}",
                    status_order,
                    index=status_order.index(status),
                    key=f"move_{row['id']}"
                )
                if new_status != status:
                    if st.button(f"Move", key=f"btn_{row['id']}"):
                        update_status(row['id'], new_status)
                        st.cache_data.clear()
                        st.rerun()
                st.markdown("---")

# ─── TREND CHART ───
st.subheader("📈 Daily Trend")
conn = sqlite3.connect(DB_PATH)
trend_df = pd.read_sql_query("""
    SELECT date(seen_at) as date, COUNT(*) as count
    FROM jobs
    WHERE seen_at > date('now', '-30 days')
    GROUP BY date(seen_at)
    ORDER BY date
""", conn)
conn.close()
if not trend_df.empty:
    fig = px.line(trend_df, x='date', y='count', title='Jobs Found (Last 30 Days)',
                  markers=True, color_discrete_sequence=['#00b4d8'])
    st.plotly_chart(fig, use_container_width=True)

# ─── PAGINATED TABLE ───
st.subheader(f"📋 Job Listings ({len(filtered)} total)")
page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=1)
total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1) - 1
start = page * page_size
end = min(start + page_size, len(filtered))
page_df = filtered.iloc[start:end]

st.dataframe(
    page_df[['title', 'company', 'location', 'salary', 'score', 'status', 'source', 'posted_at', 'type', 'url']],
    use_container_width=True,
    column_config={
        "title": st.column_config.TextColumn("Title", width="medium"),
        "company": st.column_config.TextColumn("Company", width="medium"),
        "location": st.column_config.TextColumn("Location", width="small"),
        "salary": st.column_config.TextColumn("Salary", width="small"),
        "score": st.column_config.NumberColumn("Score", min_value=0, max_value=100, format="%d"),
        "status": st.column_config.TextColumn("Status"),
        "source": st.column_config.TextColumn("Source"),
        "posted_at": st.column_config.DatetimeColumn("Posted", format="YYYY-MM-DD"),
        "type": st.column_config.TextColumn("Type"),
        "url": st.column_config.LinkColumn("Apply", help="Click to apply", display_text="🔗 Apply"),
    },
    hide_index=True,
)
st.caption(f"Showing {start+1}–{end} of {len(filtered)} jobs (Page {page+1}/{total_pages})")

# ─── DETAILS ───
with st.expander("📄 Job Details & Notes"):
    if not filtered.empty:
        job_ids = filtered['id'].tolist()
        selected = st.selectbox("Select a job", job_ids)
        job = filtered[filtered['id'] == selected].iloc[0]

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Title:** {job['title']}")
            st.markdown(f"**Company:** {job['company']}")
            st.markdown(f"**Location:** {job['location']}")
            st.markdown(f"**Salary:** {job.get('salary', 'Not specified')}")
            st.markdown(f"**Score:** {job['score']}/100")
            st.markdown(f"**Status:** {job['status']}")
            st.markdown(f"**Source:** {job['source']}")
            st.markdown(f"**Posted:** {job['posted_at']}")
            st.markdown(f"**Type:** {job.get('type', 'job')}")
        with col2:
            st.markdown(f"[🔗 Apply Now]({job['url']})", unsafe_allow_html=True)
            new_status = st.selectbox(
                "Update Status",
                ['new', 'viewed', 'applied', 'interview', 'offer', 'rejected'],
                index=['new', 'viewed', 'applied', 'interview', 'offer', 'rejected'].index(job['status'])
            )
            if st.button("Update Status"):
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job['id']))
                conn.commit()
                conn.close()
                st.success(f"✅ Updated to {new_status}")
                st.cache_data.clear()
                st.rerun()

        st.subheader("📝 Notes")
        notes = job.get('notes', '')
        new_notes = st.text_area("Edit notes", value=notes, height=100)
        if st.button("💾 Save Notes"):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (new_notes, job['id']))
            conn.commit()
            conn.close()
            st.success("✅ Notes saved!")
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("No jobs to display.")

# ─── SIDEBAR ACTIONS ───
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Run Scraper Now", use_container_width=True):
    with st.spinner("Running job hunter..."):
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        if result.returncode == 0:
            st.sidebar.success("✅ Scraper finished!")
        else:
            st.sidebar.error(f"❌ Failed: {result.stderr}")
        st.cache_data.clear()
        st.rerun()

if st.sidebar.button("📥 Export Filtered Jobs as CSV"):
    csv = filtered.to_csv(index=False)
    st.sidebar.download_button("📥 Download CSV", csv, file_name=f"jobs_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")

# ─── AUTO‑REFRESH LOOP ───
if auto_refresh:
    # We'll use a placeholder to rerun after 5 minutes
    # This is a simple way; streamlit will rerun on next interaction, but we can force a rerun with a timer.
    # We'll just show a message and rely on st.cache_data TTL (60 sec) but we want 5 min.
    # Actually, we can use st.experimental_rerun() in a loop, but it's not recommended.
    # Better: We'll just display a countdown and rely on the user manually refreshing.
    st.sidebar.info("Page will refresh automatically every 5 minutes (using browser refresh).")
    # We can add a meta refresh tag:
    st.markdown('<meta http-equiv="refresh" content="300" />', unsafe_allow_html=True)

st.sidebar.markdown("---")
st.sidebar.caption(f"🕐 Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
