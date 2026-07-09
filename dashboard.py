# dashboard.py – Remote Lover v2.0 (Redesigned & Working)
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import subprocess
import os
from pathlib import Path

st.set_page_config(
    page_title="Remote Lover",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ─── CONSTANTS ───
DB_PATH = Path("data/jobs.db")
PAGE_SIZE = 12

# ─── CUSTOM CSS ───
st.markdown("""
<style>
    /* ─── Global ─── */
    .stApp {
        background: #f8f9fa;
    }
    .main > div {
        padding-top: 0;
    }
    
    /* ─── Brand Header ─── */
    .brand-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 0;
        border-bottom: 1px solid #e9ecef;
        margin-bottom: 24px;
        background: white;
        padding: 12px 24px;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .brand-left {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .brand-logo {
        font-size: 2rem;
        background: linear-gradient(135deg, #4CAF50, #2196F3);
        border-radius: 12px;
        padding: 6px 12px;
        color: white;
        font-weight: 700;
        font-size: 1.1rem;
    }
    .brand-title {
        font-size: 1.5rem;
        font-weight: 700;
        color: #1a1a2e;
        margin: 0;
    }
    .brand-title span {
        color: #4CAF50;
    }
    .brand-subtitle {
        font-size: 0.8rem;
        color: #6c757d;
        margin: 0;
    }
    .brand-stats {
        display: flex;
        gap: 24px;
        font-size: 0.85rem;
    }
    .brand-stats span {
        color: #6c757d;
    }
    .brand-stats strong {
        color: #1a1a2e;
    }
    
    /* ─── Job Cards ─── */
    .job-card {
        background: white;
        border-radius: 12px;
        padding: 16px 18px;
        margin-bottom: 12px;
        border: 1px solid #e9ecef;
        transition: all 0.2s ease;
        height: 100%;
        display: flex;
        flex-direction: column;
    }
    .job-card:hover {
        border-color: #4CAF50;
        box-shadow: 0 4px 12px rgba(76, 175, 80, 0.12);
        transform: translateY(-2px);
    }
    .job-card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 8px;
    }
    .job-card-company {
        font-size: 0.8rem;
        font-weight: 600;
        color: #4CAF50;
        text-transform: uppercase;
        letter-spacing: 0.3px;
    }
    .job-card-badge {
        font-size: 0.65rem;
        padding: 2px 10px;
        border-radius: 20px;
        font-weight: 600;
        background: #e8f5e9;
        color: #2e7d32;
    }
    .job-card-title {
        font-size: 1.05rem;
        font-weight: 600;
        color: #1a1a2e;
        margin: 4px 0 6px 0;
        line-height: 1.3;
    }
    .job-card-title a {
        color: #1a1a2e;
        text-decoration: none;
    }
    .job-card-title a:hover {
        color: #4CAF50;
    }
    .job-card-summary {
        font-size: 0.85rem;
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
        gap: 12px;
        font-size: 0.75rem;
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
        font-size: 0.9rem;
        color: #1a1a2e;
    }
    .job-card-score span {
        color: #4CAF50;
    }
    .job-card-apply {
        background: #4CAF50;
        color: white !important;
        padding: 6px 16px;
        border-radius: 6px;
        text-decoration: none;
        font-size: 0.8rem;
        font-weight: 600;
        transition: background 0.2s;
    }
    .job-card-apply:hover {
        background: #388E3C;
        color: white !important;
    }
    .job-card-saved {
        color: #f59f00;
        font-size: 0.8rem;
    }
    
    /* ─── Dark Mode ─── */
    .dark-mode .stApp {
        background: #0e1117;
    }
    .dark-mode .brand-header {
        background: #1a1e27;
        border-color: #2d2d3d;
    }
    .dark-mode .brand-title {
        color: white;
    }
    .dark-mode .brand-stats strong {
        color: white;
    }
    .dark-mode .job-card {
        background: #1a1e27;
        border-color: #2d2d3d;
    }
    .dark-mode .job-card:hover {
        border-color: #4CAF50;
    }
    .dark-mode .job-card-title {
        color: white;
    }
    .dark-mode .job-card-title a {
        color: white;
    }
    .dark-mode .job-card-summary {
        color: #adb5bd;
    }
    .dark-mode .job-card-meta {
        color: #868e96;
    }
    .dark-mode .brand-stats span {
        color: #868e96;
    }
    
    /* ─── Responsive ─── */
    @media (max-width: 768px) {
        .brand-header {
            flex-direction: column;
            align-items: flex-start;
            gap: 12px;
        }
        .brand-stats {
            flex-wrap: wrap;
            gap: 12px;
        }
        .job-card-footer {
            flex-direction: column;
            gap: 8px;
            align-items: flex-start;
        }
    }
</style>
""", unsafe_allow_html=True)

# ─── DARK MODE ───
dark = st.sidebar.toggle("🌙 Dark Mode", value=st.session_state.get("dark_mode", False))
st.session_state.dark_mode = dark
if dark:
    st.markdown('<div class="dark-mode">', unsafe_allow_html=True)

# ─── DATABASE FUNCTIONS ───
def get_db_connection():
    return sqlite3.connect(DB_PATH)

def table_exists():
    if not DB_PATH.exists():
        return False
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    result = c.fetchone() is not None
    conn.close()
    return result

def migrate_db():
    if not DB_PATH.exists():
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if not c.fetchone():
        conn.close()
        return
    c.execute("PRAGMA table_info(jobs)")
    existing = [col[1] for col in c.fetchall()]
    for col in ["salary_min", "salary_max", "salary_text", "source_url", "type", "notes", "saved", "content"]:
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()

@st.cache_data(ttl=120)
def load_jobs():
    if not DB_PATH.exists() or not table_exists():
        return pd.DataFrame()
    conn = get_db_connection()
    df = pd.read_sql_query("""
        SELECT id, title, company, location, url, source,
               score, status, type, posted_at, saved,
               salary_min, salary_max, salary_text, content
        FROM jobs
        ORDER BY score DESC
    """, conn)
    conn.close()
    if df.empty:
        return df
    df['status'] = df['status'].fillna('new').replace('', 'new')
    df['type'] = df['type'].fillna('job')
    df['score'] = df['score'].fillna(0)
    df['salary_display'] = df.apply(
        lambda r: f"${r['salary_min']:,.0f}" if r['salary_min'] and r['salary_min'] == r['salary_max'] else
                  f"${r['salary_min']:,.0f}-${r['salary_max']:,.0f}" if r['salary_min'] and r['salary_max'] else
                  r['salary_text'] or "",
        axis=1
    )
    # Generate short summary from content
    df['summary'] = df['content'].fillna('').apply(
        lambda x: x[:180] + '...' if len(x) > 180 else x
    )
    return df

def run_scraper():
    with st.spinner("🔄 Fetching latest jobs..."):
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        if result.returncode == 0:
            st.success("✅ Scraper finished!")
            st.cache_data.clear()
            return True
        else:
            st.error(f"❌ Scraper failed:\n{result.stderr}")
            return False

# ─── MIGRATE ───
migrate_db()

# ─── HEADER ───
df = load_jobs()
total_jobs = len(df)

st.markdown(f"""
<div class="brand-header">
    <div class="brand-left">
        <div class="brand-logo">❤️</div>
        <div>
            <div class="brand-title">Remote <span>Lover</span></div>
            <div class="brand-subtitle">🌍 Remote jobs with no geo‑restrictions</div>
        </div>
    </div>
    <div class="brand-stats">
        <span>📊 <strong>{total_jobs}</strong> jobs</span>
        <span>🆕 <strong>{len(df[df['status']=='new']) if not df.empty else 0}</strong> new</span>
        <span>⭐ <strong>{df['score'].mean():.1f if not df.empty else 0}</strong> avg score</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ─── SIDEBAR FILTERS ───
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
    
    show_global_only = st.checkbox("🌍 Global‑Friendly Only", value=False)
    
    sort_by = st.selectbox(
        "Sort by",
        ["Highest Score", "Most Recent", "Easiest First (Tasks)"],
        index=0
    )
    
    # ─── RUN SCRAPER ───
    st.markdown("---")
    if st.button("🔄 Refresh Jobs", use_container_width=True):
        if run_scraper():
            st.rerun()
    
    if st.button("📥 Export CSV", use_container_width=True):
        csv = df.to_csv(index=False)
        st.download_button("Download", csv, "jobs.csv", "text/csv")

# ─── APPLY FILTERS ───
if not df.empty:
    filtered = df.copy()
    
    # Status
    if status_filter:
        filtered = filtered[filtered['status'].isin(status_filter)]
    
    # Score
    if min_score:
        filtered = filtered[filtered['score'] >= min_score]
    
    # Type
    if job_type:
        filtered = filtered[filtered['type'].isin(job_type)]
    
    # Date
    if date_range != "All":
        days = int(date_range.split()[0])
        cutoff = datetime.now() - timedelta(days=days)
        filtered = filtered[pd.to_datetime(filtered['posted_at'], errors='coerce') >= cutoff]
    
    # Search
    if search:
        filtered = filtered[
            filtered['title'].str.lower().str.contains(search.lower(), na=False) |
            filtered['company'].str.lower().str.contains(search.lower(), na=False) |
            filtered['summary'].str.lower().str.contains(search.lower(), na=False)
        ]
    
    # Sort
    if sort_by == "Highest Score":
        filtered = filtered.sort_values('score', ascending=False)
    elif sort_by == "Most Recent":
        filtered = filtered.sort_values('posted_at', ascending=False)
    else:  # Easiest First
        filtered = filtered.sort_values(['type', 'score'], ascending=[True, False])
    
    # ─── DISPLAY JOBS ───
    total_filtered = len(filtered)
    total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
    
    # Pagination
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    with col2:
        st.write(f"Showing {min(PAGE_SIZE, total_filtered)} of {total_filtered} jobs")
    with col3:
        st.write(f"Page {page} of {total_pages}")
    
    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_filtered)
    page_df = filtered.iloc[start_idx:end_idx]
    
    # ─── JOB CARDS ───
    if not page_df.empty:
        # Use 3 columns
        cols = st.columns(3)
        for idx, (_, job) in enumerate(page_df.iterrows()):
            col = cols[idx % 3]
            with col:
                badge = "🌍 " if job.get('saved') else ""
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
                    <div class="job-card-summary">{job.get('summary', 'No description available')}</div>
                    <div class="job-card-meta">
                        <span>📍 {job.get('location', 'Remote')}</span>
                        <span>💰 {job.get('salary_display', 'N/A')}</span>
                        <span>📡 {job.get('source', 'unknown')}</span>
                    </div>
                    <div class="job-card-footer">
                        <div class="job-card-score">
                            Score: <span style="color:{score_color}">{score}</span>
                        </div>
                        <a href="{job.get('url', '#')}" target="_blank" class="job-card-apply">Apply →</a>
                    </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("No jobs match your filters. Try adjusting them.")

else:
    # ─── EMPTY STATE ───
    st.warning("📭 No jobs found. Run the scraper to get started.")
    if st.button("🚀 Run Scraper Now"):
        run_scraper()
        st.rerun()

# ─── FOOTER ───
st.markdown("---")
st.caption("❤️ Remote Lover · Find your next remote job · Updated: " + datetime.now().strftime("%H:%M:%S"))

if dark:
    st.markdown('</div>', unsafe_allow_html=True)
