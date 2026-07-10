# app_dash.py – Remote Lover v3.0 (Fully fixed & improved)
import dash
from dash import dcc, html, Input, Output, State, callback, no_update, dash_table
import dash_bootstrap_components as dbc
import pandas as pd
import sqlite3
from pathlib import Path
import subprocess
import os
from datetime import datetime, timedelta
import json

# ─── CONSTANTS ───
DB_PATH = Path("data/jobs.db")
PAGE_SIZE = 12

# ─── DATABASE SETUP ───
def ensure_db():
    """Create database and table if missing."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            source TEXT,
            score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            type TEXT DEFAULT 'job',
            posted_at TEXT,
            saved TEXT DEFAULT '0',
            salary_min INTEGER,
            salary_max INTEGER,
            salary_text TEXT DEFAULT '',
            content TEXT DEFAULT '',
            seen_at TEXT DEFAULT ''
        )
    """)
    # Add missing columns if table existed with fewer columns
    c.execute("PRAGMA table_info(jobs)")
    existing = {row[1] for row in c.fetchall()}
    for col, col_type in {
        "status": "TEXT DEFAULT 'new'",
        "type": "TEXT DEFAULT 'job'",
        "seen_at": "TEXT DEFAULT ''",
        "content": "TEXT DEFAULT ''",
        "saved": "TEXT DEFAULT '0'",
        "salary_min": "INTEGER",
        "salary_max": "INTEGER",
        "salary_text": "TEXT DEFAULT ''",
        "score": "INTEGER DEFAULT 0"
    }.items():
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
    conn.commit()
    conn.close()

# ─── DATA LOADING ───
def load_jobs():
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("""
            SELECT id, title, company, location, url, source,
                   score, status, type, posted_at, saved,
                   salary_min, salary_max, salary_text, content, seen_at
            FROM jobs
            ORDER BY score DESC
        """, conn)
    except Exception as e:
        print(f"Query error: {e}")
        df = pd.DataFrame()
    conn.close()
    if df.empty:
        return df
    # Cleanup
    df['status'] = df['status'].fillna('new').replace('', 'new')
    df['type'] = df['type'].fillna('job')
    df['score'] = df['score'].fillna(0).astype(int)
    df['seen_at'] = df['seen_at'].fillna(datetime.now().isoformat())
    # Salary display
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

# ─── APP ───
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])
app.title = "Remote Lover"

# ─── JOB CARD ───
def job_card(job):
    score = job.get('score', 0)
    score_color = "#4CAF50" if score >= 70 else "#FF9800" if score >= 40 else "#f44336"
    return dbc.Card([
        dbc.CardBody([
            html.Div([
                html.Span(job.get('company', 'Unknown'), className="text-muted small text-uppercase fw-bold"),
                html.Span(job.get('type', 'job').upper(), className="badge bg-success ms-2")
            ], className="d-flex justify-content-between align-items-start"),
            html.H5(
                html.A(job.get('title', 'Untitled'), href=job.get('url', '#'), target="_blank", className="text-dark text-decoration-none"),
                className="mt-2"
            ),
            html.P(job.get('summary', ''), className="text-muted small", style={"flex": "1"}),
            html.Div([
                html.Span(f"📍 {job.get('location', 'Remote')}", className="me-2"),
                html.Span(f"💰 {job.get('salary_display', 'N/A')}", className="me-2"),
                html.Span(f"📡 {job.get('source', '')}", className="me-2"),
            ], className="text-muted small d-flex flex-wrap gap-2"),
            html.Hr(),
            html.Div([
                html.Span(f"⭐ {score}", style={"color": score_color, "fontWeight": "bold"}),
                html.A("Apply →", href=job.get('url', '#'), target="_blank", className="btn btn-success btn-sm ms-auto"),
            ], className="d-flex justify-content-between align-items-center mt-2"),
        ], className="d-flex flex-column", style={"height": "100%"})
    ], className="h-100 shadow-sm")

# ─── SIDEBAR FILTERS ───
filters_sidebar = html.Div([
    html.H5("🔍 Filters", className="mb-3"),
    html.Label("Status"),
    dcc.Dropdown(
        id="status-filter",
        options=[
            {"label": "New", "value": "new"},
            {"label": "Viewed", "value": "viewed"},
            {"label": "Applied", "value": "applied"},
            {"label": "Interview", "value": "interview"},
            {"label": "Offer", "value": "offer"},
            {"label": "Rejected", "value": "rejected"},
        ],
        value=["new", "viewed", "applied"],
        multi=True,
        className="mb-3"
    ),
    html.Label("Min Score"),
    dcc.Slider(
        id="score-slider",
        min=0, max=100, value=0, step=5,
        marks={0: "0", 25: "25", 50: "50", 75: "75", 100: "100"},
        className="mb-3"
    ),
    html.Label("Type"),
    dcc.Dropdown(
        id="type-filter",
        options=[{"label": "Job", "value": "job"}, {"label": "Task", "value": "task"}],
        value=["job", "task"],
        multi=True,
        className="mb-3"
    ),
    html.Label("Date Range"),
    dcc.Dropdown(
        id="date-filter",
        options=[
            {"label": "All", "value": "All"},
            {"label": "7 days", "value": "7"},
            {"label": "30 days", "value": "30"},
            {"label": "90 days", "value": "90"},
        ],
        value="All",
        className="mb-3"
    ),
    html.Label("Search"),
    dbc.Input(id="search-input", type="text", placeholder="Search jobs...", className="mb-3"),
    html.Label("Sort by"),
    dcc.Dropdown(
        id="sort-filter",
        options=[
            {"label": "Highest Score", "value": "score"},
            {"label": "Most Recent", "value": "seen_at"},
            {"label": "Easiest First", "value": "easiest"},
        ],
        value="score",
        className="mb-3"
    ),
    html.Hr(),
    dbc.Button("🔄 Refresh Jobs", id="refresh-btn", color="primary", className="w-100 mb-2"),
    dbc.Button("📥 Export CSV", id="export-btn", color="secondary", className="w-100"),
    dcc.Download(id="download-csv"),   # <-- Proper download component
    html.Div(id="refresh-status", className="text-center mt-2 small text-muted"),
], style={"padding": "20px"})

# ─── MODALS ───
ai_blog_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("📝 AI Job Market Blog")),
    dbc.ModalBody(id="ai-blog-content", children="Click 'Generate Blog' to see insights."),
    dbc.ModalFooter([
        dbc.Button("Generate Blog", id="generate-blog-btn", color="primary", className="me-2"),
        dbc.Button("Close", id="close-ai-blog", className="ms-auto", color="secondary"),
    ])
], id="ai-blog-modal", size="lg", is_open=False)

archived_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("📦 Archived Jobs (90+ days old)")),
    dbc.ModalBody(id="archived-jobs-body"),
    dbc.ModalFooter([
        dbc.Button("Close", id="close-archived", className="ms-auto", color="secondary"),
    ])
], id="archived-modal", size="lg", is_open=False)

# ─── LAYOUT ───
app.layout = dbc.Container([
    # Hidden stores
    dcc.Store(id="full-data-store", data=[]),      # holds all jobs as JSON
    dcc.Store(id="filtered-data-store", data=[]),  # holds filtered jobs

    # Header
    dbc.Row([
        dbc.Col([
            html.Div([
                html.Div("❤️", className="bg-primary text-white rounded p-2 me-2", style={"fontSize": "1.5rem"}),
                html.H1("Remote Lover", className="h3 mb-0", style={"color": "#1a1a2e"}),
                html.Span("🌍", className="ms-2 text-muted"),
                html.Span("No geo‑restrictions", className="text-muted ms-2 d-none d-sm-inline"),
            ], className="d-flex align-items-center"),
        ], width="auto"),
        dbc.Col([
            html.Div([
                html.Span(id="total-jobs-badge", className="me-3"),
                html.Span(id="new-jobs-badge", className="me-3"),
                html.Span(id="avg-score-badge", className="me-3"),
                dbc.Button("🌙", id="dark-mode-toggle", color="light", size="sm", className="ms-2"),
            ], className="d-flex align-items-center justify-content-end flex-wrap"),
        ], width="auto", className="ms-auto"),
    ], className="bg-white p-3 rounded-3 shadow-sm mb-4 d-flex align-items-center justify-content-between flex-wrap"),

    # Main content
    dbc.Row([
        dbc.Col(filters_sidebar, xs=12, md=3, lg=2),
        dbc.Col([
            dcc.Loading(
                id="loading-jobs",
                type="default",
                children=[
                    html.Div(id="job-cards-container"),
                    html.Div([
                        html.Div(id="pagination-info", className="text-muted me-3"),
                        dbc.Pagination(
                            id="pagination-component",
                            active_page=1,
                            max_value=1,
                            size="sm",
                            className="d-inline-flex"
                        ),
                    ], className="d-flex align-items-center justify-content-center flex-wrap", id="pagination-controls"),
                ]
            )
        ], xs=12, md=9, lg=10),
    ]),

    # AI Blog & Archived buttons
    dbc.Row([
        dbc.Col([
            dbc.Button("📝 AI Job Market Blog", id="open-ai-blog", color="info", className="me-2"),
            dbc.Button("📦 Archived Jobs", id="open-archived", color="secondary"),
        ], className="text-center mt-4"),
    ]),

    ai_blog_modal,
    archived_modal,

    # Footer
    dbc.Row([
        dbc.Col([
            html.Hr(),
            html.P(f"❤️ Remote Lover · Updated: {datetime.now().strftime('%H:%M:%S')}", className="text-center text-muted small")
        ])
    ]),

], fluid=True, className="bg-light min-vh-100", id="main-container")

# ─── HELPER: FILTER DATA ───
def filter_dataframe(df, status, min_score, job_type, date_range, search, sort_by):
    if df.empty:
        return df
    filtered = df.copy()
    if status:
        filtered = filtered[filtered['status'].isin(status)]
    if min_score:
        filtered = filtered[filtered['score'] >= min_score]
    if job_type:
        filtered = filtered[filtered['type'].isin(job_type)]
    if date_range != "All":
        days = int(date_range)
        cutoff = datetime.now() - timedelta(days=days)
        filtered = filtered[pd.to_datetime(filtered['seen_at'], errors='coerce') >= cutoff]
    if search:
        search_lower = search.lower()
        filtered = filtered[
            filtered['title'].str.lower().str.contains(search_lower, na=False) |
            filtered['company'].str.lower().str.contains(search_lower, na=False)
        ]
    if sort_by == "score":
        filtered = filtered.sort_values('score', ascending=False)
    elif sort_by == "seen_at":
        filtered = filtered.sort_values('seen_at', ascending=False)
    elif sort_by == "easiest":
        filtered = filtered.sort_values(['type', 'score'], ascending=[True, False])
    return filtered

# ─── CALLBACKS ───

# 1. Load initial data on startup
@app.callback(
    Output("full-data-store", "data"),
    Output("total-jobs-badge", "children"),
    Output("new-jobs-badge", "children"),
    Output("avg-score-badge", "children"),
    Input("refresh-btn", "n_clicks"),
    prevent_initial_call=False,  # runs on load
)
def load_initial_data(n_clicks):
    # On first load or refresh, load from DB
    df = load_jobs()
    if df.empty:
        return [], "📊 0 jobs", "🆕 0 new", "⭐ 0.0 avg"
    # Update badges
    total = len(df)
    new_count = len(df[df['status'] == 'new'])
    avg_score = df['score'].mean()
    # Convert to JSON
    records = df.to_dict('records')
    return records, f"📊 {total} jobs", f"🆕 {new_count} new", f"⭐ {avg_score:.1f} avg"

# 2. Filter data whenever filters or full data changes
@app.callback(
    Output("filtered-data-store", "data"),
    Output("pagination-component", "max_value"),
    Output("pagination-component", "active_page"),
    Output("pagination-info", "children"),
    Input("full-data-store", "data"),
    Input("status-filter", "value"),
    Input("score-slider", "value"),
    Input("type-filter", "value"),
    Input("date-filter", "value"),
    Input("search-input", "value"),
    Input("sort-filter", "value"),
    State("pagination-component", "active_page"),
)
def update_filtered_data(full_data_json, status, min_score, job_type, date_range, search, sort_by, current_page):
    if not full_data_json:
        return [], 1, 1, "No jobs found"
    df = pd.DataFrame(full_data_json)
    filtered = filter_dataframe(df, status, min_score, job_type, date_range, search, sort_by)
    total = len(filtered)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total > 0 else 1
    # Ensure current page is valid
    current_page = min(current_page, total_pages) if current_page else 1
    # Store filtered data as JSON
    filtered_records = filtered.to_dict('records') if not filtered.empty else []
    # Update pagination info
    start = (current_page - 1) * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    info = f"Showing {start+1}–{end} of {total} jobs" if total > 0 else "No jobs match your filters"
    return filtered_records, total_pages, current_page, info

# 3. Render cards based on filtered data and current page
@app.callback(
    Output("job-cards-container", "children"),
    Input("filtered-data-store", "data"),
    Input("pagination-component", "active_page"),
)
def render_cards(filtered_json, page):
    if not filtered_json:
        return html.Div("No jobs found. Run the scraper.", className="text-center text-muted")
    df = pd.DataFrame(filtered_json)
    total = len(df)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    page_df = df.iloc[start:end]
    if page_df.empty:
        return html.Div("No jobs on this page.", className="text-center text-muted")
    cards = []
    for _, job in page_df.iterrows():
        cards.append(dbc.Col(job_card(job), xs=12, sm=6, lg=4, className="mb-4"))
    return html.Div(cards, className="row")

# 4. Refresh button – run scraper and update full data
@app.callback(
    Output("full-data-store", "data", allow_duplicate=True),
    Output("total-jobs-badge", "children", allow_duplicate=True),
    Output("new-jobs-badge", "children", allow_duplicate=True),
    Output("avg-score-badge", "children", allow_duplicate=True),
    Output("refresh-status", "children"),
    Input("refresh-btn", "n_clicks"),
    prevent_initial_call=True,
)
def run_scraper(n_clicks):
    if n_clicks is None:
        return no_update
    try:
        result = subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return no_update, no_update, no_update, no_update, "❌ Scraper failed"
        df = load_jobs()
        if df.empty:
            return [], "📊 0 jobs", "🆕 0 new", "⭐ 0.0 avg", "⚠️ No jobs found"
        records = df.to_dict('records')
        total = len(df)
        new_count = len(df[df['status'] == 'new'])
        avg_score = df['score'].mean()
        return records, f"📊 {total} jobs", f"🆕 {new_count} new", f"⭐ {avg_score:.1f} avg", "✅ Refreshed!"
    except Exception as e:
        return no_update, no_update, no_update, no_update, f"❌ Error: {str(e)}"

# 5. Dark mode toggle
@app.callback(
    Output("main-container", "className"),
    Input("dark-mode-toggle", "n_clicks"),
    State("main-container", "className"),
)
def toggle_dark(n, current_class):
    if n is None:
        return current_class or "bg-light min-vh-100"
    if "bg-dark" in current_class:
        return current_class.replace("bg-dark", "bg-light").replace("text-white", "")
    else:
        return current_class + " bg-dark text-white"

# 6. AI Blog modal toggle
@app.callback(
    Output("ai-blog-modal", "is_open"),
    Input("open-ai-blog", "n_clicks"),
    Input("close-ai-blog", "n_clicks"),
    State("ai-blog-modal", "is_open"),
)
def toggle_ai_blog(open_clicks, close_clicks, is_open):
    if open_clicks or close_clicks:
        return not is_open
    return is_open

# 7. Generate AI Blog content
@app.callback(
    Output("ai-blog-content", "children"),
    Input("generate-blog-btn", "n_clicks"),
    State("full-data-store", "data"),
    prevent_initial_call=True,
)
def generate_blog(n, full_data):
    if not n or not full_data:
        return "Click 'Generate Blog' to see insights."
    df = pd.DataFrame(full_data)
    if df.empty:
        return "No data to generate insights."
    try:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "⚠️ GEMINI_API_KEY not set."
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-pro')
        top_companies = df['company'].value_counts().head(5).to_dict()
        top_roles = df['title'].value_counts().head(5).to_dict()
        total = len(df)
        prompt = f"Write a short blog (200-300 words) about remote job market trends based on: total jobs {total}, top companies {top_companies}, top roles {top_roles}. Use markdown."
        response = model.generate_content(prompt)
        return dcc.Markdown(response.text)
    except Exception as e:
        return f"⚠️ AI blog unavailable: {e}"

# 8. Archived modal toggle
@app.callback(
    Output("archived-modal", "is_open"),
    Input("open-archived", "n_clicks"),
    Input("close-archived", "n_clicks"),
    State("archived-modal", "is_open"),
)
def toggle_archived(open_clicks, close_clicks, is_open):
    if open_clicks or close_clicks:
        return not is_open
    return is_open

# 9. Load archived jobs
@app.callback(
    Output("archived-jobs-body", "children"),
    Input("open-archived", "n_clicks"),
    prevent_initial_call=True,
)
def load_archived(n):
    if not n:
        return html.Div("No archived jobs.")
    try:
        conn = sqlite3.connect(DB_PATH)
        archived_df = pd.read_sql_query("SELECT title, company, location, posted_at, archived_at FROM jobs_archive ORDER BY archived_at DESC LIMIT 100", conn)
        conn.close()
        if archived_df.empty:
            return html.Div("No archived jobs yet.")
        return dbc.Table.from_dataframe(archived_df, striped=True, bordered=True, hover=True, size="sm")
    except Exception:
        return html.Div("Error loading archived jobs.")

# 10. Export CSV
@app.callback(
    Output("download-csv", "data"),
    Input("export-btn", "n_clicks"),
    State("full-data-store", "data"),
    prevent_initial_call=True,
)
def export_csv(n_clicks, full_data):
    if not n_clicks or not full_data:
        return no_update
    df = pd.DataFrame(full_data)
    if df.empty:
        return no_update
    return dict(
        content=df.to_csv(index=False),
        filename=f"remote_jobs_{datetime.now().strftime('%Y%m%d')}.csv"
    )

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
