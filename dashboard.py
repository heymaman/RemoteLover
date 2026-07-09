# dashboard.py – Remote Lover v33.0
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import subprocess
import os
import json
from pathlib import Path

# ─── PAGE CONFIG ───
st.set_page_config(
    page_title="Remote Lover",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── CONSTANTS ───
DB_PATH = Path("data/jobs.db")
GLOBAL_FRIENDLY_COMPANIES = [
    "gitlab", "stripe", "figma", "notion", "linear", "supabase", "airbnb",
    "vercel", "railway", "anthropic", "deepmind", "shopify", "discord",
    "spotify", "dropbox", "datadog", "elastic", "mongodb", "scale ai",
    "brex", "coursera", "amplitude"
]
global_friendly_lower = [c.lower() for c in GLOBAL_FRIENDLY_COMPANIES]

# ─── SESSION STATE ───
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

# ─── CUSTOM CSS FOR BRANDING ───
st.markdown("""
<style>
    /* Brand Header */
    .brand-header {
        display: flex;
        align-items: center;
        gap: 15px;
        padding: 20px 0 10px 0;
        border-bottom: 2px solid #4CAF50;
        margin-bottom: 20px;
    }
    .brand-logo {
        font-size: 3rem;
        line-height: 1;
        background: linear-gradient(135deg, #4CAF50, #2196F3);
        border-radius: 50%;
        padding: 10px 15px;
        color: white;
        display: inline-block;
        width: 70px;
        height: 70px;
        text-align: center;
        box-shadow: 0 4px 15px rgba(76, 175, 80, 0.3);
    }
    .brand-title {
        font-size: 2.8rem;
        font-weight: 800;
        letter-spacing: -1px;
        background: linear-gradient(135deg, #4CAF50, #2196F3);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin: 0;
    }
    .brand-subtitle {
        font-size: 0.95rem;
        color: #888;
        margin: 0;
        -webkit-text-fill-color: #888;
    }
    /* Dark mode support */
    .dark-mode .brand-title {
        background: linear-gradient(135deg, #66BB6A, #42A5F5);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .dark-mode .brand-subtitle {
        color: #aaa;
    }
</style>
""", unsafe_allow_html=True)

# ─── DARK MODE ───
dark = st.sidebar.toggle("🌙 Dark Mode", value=st.session_state.dark_mode)
st.session_state.dark_mode = dark
if dark:
    st.markdown("""
        <style>
        .stApp { background: #0e1117; color: white; }
        .stSidebar { background: #1a1e27; }
        .stDataFrame { background: #0e1117; }
        .dark-mode { color: #fff; }
        </style>
    """, unsafe_allow_html=True)

# ─── BRAND HEADER ───
st.markdown(f"""
<div class="brand-header">
    <div class="brand-logo">❤️</div>
    <div>
        <h1 class="brand-title">Remote Lover</h1>
        <p class="brand-subtitle">🌍 Find your next remote job – no geo‑restrictions</p>
    </div>
</div>
""", unsafe_allow_html=True)

# ─── DATABASE MIGRATION ───
def migrate_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if not c.fetchone():
        conn.close()
        return
    c.execute("PRAGMA table_info(jobs)")
    existing = [col[1] for col in c.fetchall()]
    for col, dtype in [("salary_min", "INTEGER"), ("salary_max", "INTEGER"),
                       ("salary_text", "TEXT"), ("source_url", "TEXT"),
                       ("type", "TEXT"), ("notes", "TEXT"), ("score", "INTEGER"),
                       ("saved", "BOOLEAN"), ("content", "TEXT")]:
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype} DEFAULT ''")
    conn.commit()
    conn.close()

migrate_db()

# ─── LOAD DATA ───
@st.cache_data(ttl=300)
def load_filtered_data(status, min_score, job_type, date_range, search, sort_by, limit, show_global_only):
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT id, title, company, location, url, source,
               score, status, type, posted_at, saved,
               salary_min, salary_max, salary_text
        FROM jobs
        WHERE 1=1
    """
    params = []
    if status:
        query += f" AND status IN ({','.join(['?']*len(status))})"
        params.extend(status)
    if min_score:
        query += " AND score >= ?"
        params.append(min_score)
    if job_type:
        query += f" AND type IN ({','.join(['?']*len(job_type))})"
        params.extend(job_type)
    if search:
        query += " AND (title LIKE ? OR company LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if date_range != "All":
        days = int(date_range)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        query += " AND seen_at >= ?"
        params.append(cutoff)

    if sort_by == "Easiest first (tasks then jobs)":
        order = "CASE type WHEN 'task' THEN 0 WHEN 'job' THEN 1 END, score DESC"
    elif sort_by == "Highest score":
        order = "score DESC"
    else:
        order = "seen_at DESC"
    query += f" ORDER BY {order} LIMIT {limit}"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if show_global_only:
        df = df[df['company'].str.lower().apply(lambda x: any(gc in x for gc in global_friendly_lower))]

    df['salary_display'] = df.apply(
        lambda r: f"${r['salary_min']:,.0f}" if r['salary_min'] and r['salary_min'] == r['salary_max'] else
                  f"${r['salary_min']:,.0f}-${r['salary_max']:,.0f}" if r['salary_min'] and r['salary_max'] else
                  r['salary_text'] or "",
        axis=1
    )
    df['global_friendly'] = df['company'].str.lower().apply(lambda x: any(gc in x for gc in global_friendly_lower))
    df['🌍'] = df['global_friendly'].apply(lambda x: "🌍" if x else "")
    df['saved_display'] = df['saved'].apply(lambda x: "⭐" if x else "")
    return df

def has_jobs_table():
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    result = c.fetchone() is not None
    conn.close()
    return result

# ─── SIDEBAR FILTERS ───
with st.sidebar.form("filter_form"):
    st.header("🔍 Filters")
    status = st.multiselect(
        "Status",
        ["new", "viewed", "applied", "interview", "offer", "rejected"],
        default=["new", "viewed", "applied"]
    )
    min_score = st.slider("⭐ Min Score", 0, 100, 0)
    job_type = st.multiselect("Type", ["job", "task"], default=["job", "task"])
    tasks_first = st.checkbox("🧩 Tasks First", value=True)
    date_range = st.selectbox("📅 Date Range", ["7", "30", "90", "All"], index=0)
    search = st.text_input("🔎 Search")
    show_global_only = st.checkbox("🌍 Global‑Friendly Only", value=False)
    sort_by = st.selectbox(
        "Sort by",
        ["Easiest first (tasks then jobs)", "Highest score", "Most recent"],
        index=0
    )
    limit = st.slider("Max results", 50, 500, 200, step=50)
    submitted = st.form_submit_button("Apply Filters")

# ─── CHECK DB ───
if not has_jobs_table():
    st.warning("🚫 No data – run the scraper first.")
    if st.button("🚀 Run Scraper Now"):
        with st.spinner("Fetching jobs..."):
            result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
            if result.returncode == 0:
                st.success("✅ Scraper finished! Refresh.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ Failed:\n{result.stderr}")
    st.stop()

df = load_filtered_data(status, min_score, job_type, date_range, search, sort_by, limit, show_global_only)

# ─── TASKS FIRST ───
if tasks_first and not df.empty:
    df['type_order'] = df['type'].map({'task': 0, 'job': 1})
    df = df.sort_values(['type_order', 'score'], ascending=[True, False])
    df = df.drop(columns=['type_order'])

# ─── METRICS ───
conn = sqlite3.connect(DB_PATH)
total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
new = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0]
applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0]
interview = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='interview'").fetchone()[0]
conn.close()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("📊 Total", total, delta=f"+{new} new" if new > 0 else "")
c2.metric("🆕 New", new)
c3.metric("✅ Applied", applied)
c4.metric("🎯 Interview", interview)
c5.metric("🏆 Avg Score", f"{df['score'].mean():.1f}" if not df.empty else "0")

st.markdown("---")

# ─── TOP CARDS ───
st.subheader("🏆 Top Opportunities")
if not df.empty:
    top_jobs = df.sort_values('score', ascending=False).head(6)
    cols = st.columns(3)
    for i, (_, job) in enumerate(top_jobs.iterrows()):
        badge = "🌍 " if job['global_friendly'] else ""
        with cols[i % 3]:
            st.markdown(f"""
            <div style="background: #1e1e2e; padding: 10px; border-radius: 8px; 
                        margin-bottom: 6px; border-left: 4px solid #4CAF50;">
                <b>{badge}{job['title'][:40]}</b><br>
                🏢 {job['company']}<br>
                📍 {job['location']}<br>
                ⭐ Score: {job['score']}<br>
                <a href="{job['url']}" target="_blank" style="color: #4CAF50;">Apply →</a>
            </div>
            """, unsafe_allow_html=True)
else:
    st.info("No jobs match your filters.")

# ─── DATA TABLE ───
st.subheader(f"📋 All Jobs ({len(df)} shown)")
st.dataframe(
    df[['title', 'company', 'location', 'salary_display', 'score', 'status', 'type', '🌍', 'saved_display', 'url']],
    use_container_width=True,
    column_config={
        "title": "Title",
        "company": "Company",
        "location": "Location",
        "salary_display": "Salary",
        "score": st.column_config.NumberColumn("Score", min_value=0, max_value=100),
        "status": "Status",
        "type": "Type",
        "🌍": st.column_config.TextColumn("🌍"),
        "saved_display": st.column_config.TextColumn("⭐"),
        "url": st.column_config.LinkColumn("Apply", display_text="🔗 Apply"),
    },
    hide_index=True,
)

# ─── SOURCE BREAKDOWN ───
st.sidebar.subheader("📡 Sources")
source_counts = df['source'].value_counts()
for src, count in source_counts.items():
    st.sidebar.write(f"🔹 {src}: {count}")

# ─── JOB DETAILS ───
with st.expander("📄 Job Details"):
    if not df.empty:
        selected = st.selectbox("Select a job", df['id'].tolist())
        job = df[df['id'] == selected].iloc[0]
        badge = "🌍 " if job['global_friendly'] else ""
        st.markdown(f"""
        **{badge}{job['title']}**  
        **Company:** {job['company']}  
        **Location:** {job['location']}  
        **Salary:** {job['salary_display']}  
        **Score:** {job['score']}/100  
        **Status:** {job['status']}  
        **Type:** {'🧩 Task' if job['type']=='task' else '💼 Job'}  
        [Apply Now]({job['url']})
        """)
        saved = job.get('saved', False)
        if st.button("⭐ Save" if not saved else "Unsave"):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE jobs SET saved = ? WHERE id = ?", (not saved, job['id']))
            conn.commit()
            conn.close()
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("No jobs to display.")

# ─── SIDEBAR ACTIONS ───
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Run Scraper"):
    with st.spinner("Fetching jobs..."):
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        if result.returncode == 0:
            st.sidebar.success("✅ Scraper finished!")
        else:
            st.sidebar.error(f"❌ Failed:\n{result.stderr}")
        st.cache_data.clear()
        st.rerun()

if st.sidebar.button("📥 Export CSV"):
    csv = df.to_csv(index=False)
    st.sidebar.download_button("Download", csv, f"jobs_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

st.sidebar.info("💡 Start with the 🏆 Top Opportunities – easiest to get started.")
st.sidebar.caption(f"🕐 Updated: {datetime.now().strftime('%H:%M:%S')}")# dashboard.py – v30.0 (Efficient & Fast)
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import subprocess
import os
from pathlib import Path

st.set_page_config(page_title="Remote Jobs", page_icon="🌍", layout="wide")

GLOBAL_FRIENDLY_COMPANIES = [
    "gitlab", "stripe", "figma", "notion", "linear", "supabase", "airbnb",
    "vercel", "railway", "anthropic", "deepmind", "shopify", "discord",
    "spotify", "dropbox", "datadog", "elastic", "mongodb", "scale ai",
    "brex", "coursera", "amplitude"
]
global_friendly_lower = [c.lower() for c in GLOBAL_FRIENDLY_COMPANIES]

DB_PATH = Path("data/jobs.db")
DB_PATH.parent.mkdir(exist_ok=True)

def migrate_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if not c.fetchone():
        conn.close()
        return
    c.execute("PRAGMA table_info(jobs)")
    existing = [col[1] for col in c.fetchall()]
    for col, dtype in [("salary_min", "INTEGER"), ("salary_max", "INTEGER"),
                       ("salary_text", "TEXT"), ("source_url", "TEXT"),
                       ("type", "TEXT"), ("notes", "TEXT"), ("score", "INTEGER"),
                       ("saved", "BOOLEAN"), ("content", "TEXT")]:
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype} DEFAULT ''")
    conn.commit()
    conn.close()

migrate_db()

@st.cache_data(ttl=300)
def load_filtered_data(status, min_score, job_type, date_range, search, sort_by, limit, show_global_only):
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT id, title, company, location, url, source,
               score, status, type, posted_at, saved,
               salary_min, salary_max, salary_text
        FROM jobs
        WHERE 1=1
    """
    params = []
    if status:
        query += f" AND status IN ({','.join(['?']*len(status))})"
        params.extend(status)
    if min_score:
        query += " AND score >= ?"
        params.append(min_score)
    if job_type:
        query += f" AND type IN ({','.join(['?']*len(job_type))})"
        params.extend(job_type)
    if search:
        query += " AND (title LIKE ? OR company LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if date_range != "All":
        days = int(date_range)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        query += " AND seen_at >= ?"
        params.append(cutoff)

    if sort_by == "Easiest first (tasks then jobs)":
        order = "CASE type WHEN 'task' THEN 0 WHEN 'job' THEN 1 END, score DESC"
    elif sort_by == "Highest score":
        order = "score DESC"
    else:
        order = "seen_at DESC"
    query += f" ORDER BY {order} LIMIT {limit}"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if show_global_only:
        df = df[df['company'].str.lower().apply(lambda x: any(gc in x for gc in global_friendly_lower))]

    df['salary_display'] = df.apply(
        lambda r: f"${r['salary_min']:,.0f}" if r['salary_min'] and r['salary_min'] == r['salary_max'] else
                  f"${r['salary_min']:,.0f}-${r['salary_max']:,.0f}" if r['salary_min'] and r['salary_max'] else
                  r['salary_text'] or "",
        axis=1
    )
    df['global_friendly'] = df['company'].str.lower().apply(lambda x: any(gc in x for gc in global_friendly_lower))
    df['🌍'] = df['global_friendly'].apply(lambda x: "🌍" if x else "")
    df['saved_display'] = df['saved'].apply(lambda x: "⭐" if x else "")
    return df

def has_jobs_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    result = c.fetchone() is not None
    conn.close()
    return result

dark = st.sidebar.toggle("🌙 Dark Mode", value=st.session_state.get("dark_mode", False))
st.session_state.dark_mode = dark
if dark:
    st.markdown("""
        <style>
        .stApp { background: #0e1117; color: white; }
        .stSidebar { background: #1a1e27; }
        </style>
    """, unsafe_allow_html=True)

st.markdown("""
<div style="background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460); padding: 20px; border-radius: 10px; margin-bottom: 20px; text-align: center;">
    <h1 style="color: white; margin: 0;">🌍 Remote Jobs for Everyone</h1>
    <p style="color: #a8b5c8; margin: 0;">Tasks · Data Entry · Support · Early‑Career</p>
</div>
""", unsafe_allow_html=True)

with st.sidebar.form("filter_form"):
    st.header("🔍 Filters")
    status = st.multiselect(
        "Status",
        ["new", "viewed", "applied", "interview", "offer", "rejected"],
        default=["new", "viewed", "applied"]
    )
    min_score = st.slider("Min Score", 0, 100, 0)
    job_type = st.multiselect("Type", ["job", "task"], default=["job", "task"])
    tasks_first = st.checkbox("🧩 Tasks First", value=True)
    date_range = st.selectbox("Date Range", ["7", "30", "90", "All"], index=0)
    search = st.text_input("Search")
    show_global_only = st.checkbox("🌍 Global‑Friendly Only", value=False)
    sort_by = st.selectbox(
        "Sort by",
        ["Easiest first (tasks then jobs)", "Highest score", "Most recent"],
        index=0
    )
    limit = st.slider("Max results", 50, 500, 200, step=50)
    submitted = st.form_submit_button("Apply Filters")

if not has_jobs_table():
    st.warning("🚫 No data – run the scraper first.")
    if st.button("🚀 Run Scraper Now"):
        with st.spinner("Fetching jobs..."):
            result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
            if result.returncode == 0:
                st.success("✅ Scraper finished! Refresh.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ Failed:\n{result.stderr}")
    st.stop()

df = load_filtered_data(status, min_score, job_type, date_range, search, sort_by, limit, show_global_only)

if tasks_first and not df.empty:
    df['type_order'] = df['type'].map({'task': 0, 'job': 1})
    df = df.sort_values(['type_order', 'score'], ascending=[True, False])
    df = df.drop(columns=['type_order'])

conn = sqlite3.connect(DB_PATH)
total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
new = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0]
applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0]
interview = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='interview'").fetchone()[0]
conn.close()

c1, c2, c3, c4 = st.columns(4)
c1.metric("📊 Total", total, delta=f"+{new} new" if new > 0 else "")
c2.metric("🆕 New", new)
c3.metric("✅ Applied", applied)
c4.metric("🎯 Interview", interview)

st.markdown("---")

st.subheader("🏆 Top Opportunities")
if not df.empty:
    top_jobs = df.sort_values('score', ascending=False).head(6)
    cols = st.columns(3)
    for i, (_, job) in enumerate(top_jobs.iterrows()):
        badge = "🌍 " if job['global_friendly'] else ""
        with cols[i % 3]:
            st.markdown(f"""
            <div style="background: #1e1e2e; padding: 10px; border-radius: 8px; margin-bottom: 6px; border-left: 4px solid #4CAF50;">
                <b>{badge}{job['title'][:40]}</b><br>
                🏢 {job['company']}<br>
                📍 {job['location']}<br>
                ⭐ Score: {job['score']}<br>
                <a href="{job['url']}" target="_blank" style="color: #4CAF50;">Apply →</a>
            </div>
            """, unsafe_allow_html=True)
else:
    st.info("No jobs match your filters.")

st.subheader(f"📋 All Jobs ({len(df)} shown)")
st.dataframe(
    df[['title', 'company', 'location', 'salary_display', 'score', 'status', 'type', '🌍', 'saved_display', 'url']],
    use_container_width=True,
    column_config={
        "title": "Title",
        "company": "Company",
        "location": "Location",
        "salary_display": "Salary",
        "score": st.column_config.NumberColumn("Score", min_value=0, max_value=100),
        "status": "Status",
        "type": "Type",
        "🌍": st.column_config.TextColumn("🌍"),
        "saved_display": st.column_config.TextColumn("⭐"),
        "url": st.column_config.LinkColumn("Apply", display_text="🔗 Apply"),
    },
    hide_index=True,
)

st.sidebar.subheader("📡 Sources")
source_counts = df['source'].value_counts()
for src, count in source_counts.items():
    st.sidebar.write(f"🔹 {src}: {count}")

with st.expander("📄 Job Details"):
    if not df.empty:
        selected = st.selectbox("Select a job", df['id'].tolist())
        job = df[df['id'] == selected].iloc[0]
        badge = "🌍 " if job['global_friendly'] else ""
        st.markdown(f"""
        **{badge}{job['title']}**  
        **Company:** {job['company']}  
        **Location:** {job['location']}  
        **Salary:** {job['salary_display']}  
        **Score:** {job['score']}/100  
        **Status:** {job['status']}  
        **Type:** {'🧩 Task' if job['type']=='task' else '💼 Job'}  
        [Apply Now]({job['url']})
        """)
        saved = job.get('saved', False)
        if st.button("⭐ Save" if not saved else "Unsave"):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE jobs SET saved = ? WHERE id = ?", (not saved, job['id']))
            conn.commit()
            conn.close()
            st.cache_data.clear()
            st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Run Scraper"):
    with st.spinner("Fetching jobs..."):
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        if result.returncode == 0:
            st.sidebar.success("✅ Scraper finished!")
        else:
            st.sidebar.error(f"❌ Failed:\n{result.stderr}")
        st.cache_data.clear()
        st.rerun()

if st.sidebar.button("📥 Export CSV"):
    csv = df.to_csv(index=False)
    st.sidebar.download_button("Download", csv, f"jobs_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

st.sidebar.info("💡 Start with the 🏆 Top Opportunities – easiest to get started.")
st.sidebar.caption(f"🕐 Updated: {datetime.now().strftime('%H:%M:%S')}")
