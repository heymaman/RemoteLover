# dashboard.py – Remote Lover v3.0 (Working)
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import subprocess
import os
from pathlib import Path

# ─── PAGE CONFIG ───
st.set_page_config(
    page_title="Remote Lover",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = Path("data/jobs.db")
PAGE_SIZE = 12

# ─── CUSTOM CSS ───
st.markdown("""
<style>
    /* ─── Reset & Base ─── */
    .stApp { background: #f5f7fa; }
    .main > div { padding: 0 1rem; }
    
    /* ─── Brand Header ─── */
    .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: white;
        padding: 16px 24px;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        margin-bottom: 20px;
        flex-wrap: wrap;
        gap: 12px;
    }
    .header-left { display: flex; align-items: center; gap: 14px; }
    .logo {
        background: linear-gradient(135deg, #4CAF50, #2196F3);
        border-radius: 10px;
        padding: 8px 14px;
        color: white;
        font-weight: 700;
        font-size: 1.1rem;
    }
    .title { font-size: 1.4rem; font-weight: 700; color: #1a1a2e; margin: 0; }
    .title span { color: #4CAF50; }
    .subtitle { font-size: 0.8rem; color: #6c757d; margin: 0; }
    .stats { display: flex; gap: 20px; font-size: 0.85rem; flex-wrap: wrap; }
    .stats span { color: #6c757d; }
    .stats strong { color: #1a1a2e; }
    
    /* ─── Job Cards ─── */
    .job-card {
        background: white;
        border-radius: 10px;
        padding: 16px 18px;
        border: 1px solid #e9ecef;
        transition: all 0.2s ease;
        height: 100%;
        display: flex;
        flex-direction: column;
    }
    .job-card:hover {
        border-color: #4CAF50;
        box-shadow: 0 4px 12px rgba(76,175,80,0.1);
        transform: translateY(-2px);
    }
    .job-card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 4px;
    }
    .job-card-company {
        font-size: 0.7rem;
        font-weight: 600;
        color: #4CAF50;
        text-transform: uppercase;
        letter-spacing: 0.3px;
    }
    .job-card-badge {
        font-size: 0.6rem;
        padding: 2px 10px;
        border-radius: 20px;
        font-weight: 600;
        background: #e8f5e9;
        color: #2e7d32;
    }
    .job-card-title {
        font-size: 1rem;
        font-weight: 600;
        color: #1a1a2e;
        margin: 4px 0 6px 0;
        line-height: 1.3;
    }
    .job-card-title a {
        color: #1a1a2e;
        text-decoration: none;
    }
    .job-card-title a:hover { color: #4CAF50; }
    .job-card-summary {
        font-size: 0.8rem;
        color: #495057;
        line-height: 1.5;
        margin: 6px 0 10px 0;
        flex-grow: 1;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .job-card-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        font-size: 0.7rem;
        color: #6c757d;
        margin: 6px 0 10px 0;
    }
    .job-card-meta span {
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .job-card-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 8px;
        padding-top: 10px;
        border-top: 1px solid #f1f3f5;
    }
    .job-card-score {
        font-weight: 700;
        font-size: 0.85rem;
    }
    .job-card-apply {
        background: #4CAF50;
        color: white !important;
        padding: 5px 14px;
        border-radius: 6px;
        text-decoration: none;
        font-size: 0.75rem;
        font-weight: 600;
        transition: background 0.2s;
    }
    .job-card-apply:hover { background: #388E3C; }
    
    /* ─── Dark Mode ─── */
    .dark .stApp { background: #0e1117; }
    .dark .header { background: #1a1e27; border-color: #2d2d3d; }
    .dark .title { color: white; }
    .dark .stats strong { color: white; }
    .dark .job-card { background: #1a1e27; border-color: #2d2d3d; }
    .dark .job-card-title { color: white; }
    .dark .job-card-title a { color: white; }
    .dark .job-card-summary { color: #adb5bd; }
    .dark .job-card-meta { color: #868e96; }
    .dark .stats span { color: #868e96; }
    
    /* ─── Responsive ─── */
    @media (max-width: 768px) {
        .header { flex-direction: column; align-items: flex-start; }
        .stats { flex-wrap: wrap; }
    }
</style>
""", unsafe_allow_html=True)

# ─── DARK MODE ───
dark_mode = st.sidebar.toggle("🌙 Dark Mode", value=st.session_state.get("dark_mode", False))
st.session_state.dark_mode = dark_mode
if dark_mode:
    st.markdown('<div class="dark">', unsafe_allow_html=True)

# ─── DATABASE FUNCTIONS ───
@st.cache_resource
def get_db():
    return sqlite3.connect(DB_PATH)

def table_exists():
    if not DB_PATH.exists():
        return False
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    result = c.fetchone() is not None
    conn.close()
    return result

def migrate_db():
    if not DB_PATH.exists():
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if not c.fetchone():
        conn.close()
        return
    c.execute("PRAGMA table_info(jobs)")
    existing = [row[1] for row in c.fetchall()]
    required = ["status", "score", "type", "seen_at", "content", "saved"]
    for col in required:
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()

@st.cache_data(ttl=120)
def load_jobs():
    if not DB_PATH.exists() or not table_exists():
        return pd.DataFrame()
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT id, title, company, location, url, source,
               score, status, type, posted_at, saved,
               salary_min, salary_max, salary_text, content, seen_at
        FROM jobs
        ORDER BY score DESC
    """, conn)
    conn.close()
    if df.empty:
        return df
    df['status'] = df['status'].fillna('new').replace('', 'new')
    df['type'] = df['type'].fillna('job')
    df['score'] = df['score'].fillna(0).astype(int)
    df['seen_at'] = df['seen_at'].fillna(datetime.now().isoformat())
    df['salary_display'] = df.apply(
        lambda r: f"${r['salary_min']:,.0f}" if r['salary_min'] and r['salary_min'] == r['salary_max'] else
                  f"${r['salary_min']:,.0f}-${r['salary_max']:,.0f}" if r['salary_min'] and r['salary_max'] else
                  r['salary_text'] or "",
        axis=1
    )
    df['summary'] = df['content'].fillna('').apply(
        lambda x: (x[:180] + '...') if len(x) > 180 else x
    )
    return df

def run_scraper():
    with st.spinner("🔄 Fetching jobs..."):
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        if result.returncode == 0:
            st.success("✅ Scraper finished!")
            st.cache_data.clear()
            return True
        st.error(f"❌ Scraper failed:\n{result.stderr}")
        return False

# ─── MIGRATE & LOAD ───
migrate_db()
df = load_jobs()
total_jobs = len(df)

# ─── HEADER ───
avg_score = df['score'].mean() if not df.empty else 0
new_count = len(df[df['status'] == 'new']) if not df.empty else 0

st.markdown(f"""
<div class="header">
    <div class="header-left">
        <div class="logo">❤️</div>
        <div>
            <div class="title">Remote <span>Lover</span></div>
            <div class="subtitle">🌍 Remote jobs · No geo‑restrictions</div>
        </div>
    </div>
    <div class="stats">
        <span>📊 <strong>{total_jobs}</strong> jobs</span>
        <span>🆕 <strong>{new_count}</strong> new</span>
        <span>⭐ <strong>{avg_score:.1f}</strong> avg score</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ─── SIDEBAR ───
with st.sidebar:
    st.header("🔍 Filters")
    
    status_filter = st.multiselect(
        "Status",
        ["new", "viewed", "applied", "interview", "offer", "rejected"],
        default=["new", "viewed", "applied"]
    )
    
    min_score = st.slider("⭐ Min Score", 0, 100, 0)
    
    job_type = st.multiselect("Type", ["job", "task"], default=["job", "task"])
    
    date_range = st.selectbox("Date Range", ["All", "7 days", "30 days", "90 days"], index=0)
    
    search = st.text_input("🔎 Search", placeholder="Search jobs...")
    
    sort_by = st.selectbox(
        "Sort by",
        ["Highest Score", "Most Recent", "Easiest First"],
        index=0
    )
    
    st.markdown("---")
    
    if st.button("🔄 Refresh Jobs", use_container_width=True):
        if run_scraper():
            st.rerun()
    
    if st.button("📥 Export CSV", use_container_width=True):
        csv = df.to_csv(index=False)
        st.download_button("Download", csv, "jobs.csv", "text/csv")

# ─── FILTER DATA ───
if df.empty:
    st.warning("📭 No jobs found. Run the scraper first.")
    if st.button("🚀 Run Scraper Now"):
        run_scraper()
        st.rerun()
    st.stop()

filtered = df.copy()

if status_filter:
    filtered = filtered[filtered['status'].isin(status_filter)]
if min_score:
    filtered = filtered[filtered['score'] >= min_score]
if job_type:
    filtered = filtered[filtered['type'].isin(job_type)]
if date_range != "All":
    days = int(date_range.split()[0])
    cutoff = datetime.now() - timedelta(days=days)
    filtered = filtered[pd.to_datetime(filtered['seen_at'], errors='coerce') >= cutoff]
if search:
    filtered = filtered[
        filtered['title'].str.lower().str.contains(search.lower(), na=False) |
        filtered['company'].str.lower().str.contains(search.lower(), na=False)
    ]

if sort_by == "Highest Score":
    filtered = filtered.sort_values('score', ascending=False)
elif sort_by == "Most Recent":
    filtered = filtered.sort_values('seen_at', ascending=False)
else:
    filtered = filtered.sort_values(['type', 'score'], ascending=[True, False])

total_filtered = len(filtered)

# ─── PAGINATION ───
total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
start_idx = (page - 1) * PAGE_SIZE
end_idx = min(start_idx + PAGE_SIZE, total_filtered)
page_df = filtered.iloc[start_idx:end_idx]

st.caption(f"Showing {len(page_df)} of {total_filtered} jobs (Page {page}/{total_pages})")

# ─── JOB CARDS ───
if not page_df.empty:
    cols = st.columns(3)
    for idx, (_, job) in enumerate(page_df.iterrows()):
        with cols[idx % 3]:
            score = job.get('score', 0)
            score_color = "#4CAF50" if score >= 70 else "#FF9800" if score >= 40 else "#f44336"
            st.markdown(f"""
            <div class="job-card">
                <div class="job-card-header">
                    <span class="job-card-company">{job.get('company', 'Unknown')}</span>
                    <span class="job-card-badge">{job.get('type', 'job').upper()}</span>
                </div>
                <div class="job-card-title">
                    <a href="{job.get('url', '#')}" target="_blank">{job.get('title', 'Untitled')}</a>
                </div>
                <div class="job-card-summary">{job.get('summary', '')}</div>
                <div class="job-card-meta">
                    <span>📍 {job.get('location', 'Remote')}</span>
                    <span>💰 {job.get('salary_display', 'N/A')}</span>
                    <span>📡 {job.get('source', '')}</span>
                </div>
                <div class="job-card-footer">
                    <div class="job-card-score" style="color:{score_color}">⭐ {score}</div>
                    <a href="{job.get('url', '#')}" target="_blank" class="job-card-apply">Apply →</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
else:
    st.info("No jobs match your filters. Try adjusting them.")

# ─── FOOTER ───
st.markdown("---")
st.caption(f"❤️ Remote Lover · Updated: {datetime.now().strftime('%H:%M:%S')}")

if dark_mode:
    st.markdown('</div>', unsafe_allow_html=True)
