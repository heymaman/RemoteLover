# app_dash.py – Remote Lover v2.0 (Works with Your Existing Scraper)
import dash
from dash import dcc, html, Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc
import pandas as pd
import sqlite3
from pathlib import Path
import subprocess
import os
from datetime import datetime, timedelta
import plotly.express as px

# ─── CONSTANTS ───
DB_PATH = Path("data/jobs.db")
PAGE_SIZE = 12

# ─── DATABASE MIGRATION (Auto‑fix missing columns) ───
def migrate_db():
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if not c.fetchone():
        conn.close()
        return
    c.execute("PRAGMA table_info(jobs)")
    existing = [row[1] for row in c.fetchall()]
    # Columns the dashboard expects
    required = {
        "status": "TEXT DEFAULT 'new'",
        "type": "TEXT DEFAULT 'job'",
        "seen_at": "TEXT DEFAULT ''",
        "content": "TEXT DEFAULT ''",
        "saved": "TEXT DEFAULT '0'",
        "salary_min": "INTEGER",
        "salary_max": "INTEGER",
        "salary_text": "TEXT DEFAULT ''",
        "score": "INTEGER DEFAULT 0"
    }
    for col, col_type in required.items():
        if col not in existing:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
    conn.commit()
    conn.close()

# ─── DATA LOADING ───
def load_jobs():
    if not DB_PATH.exists():
        return pd.DataFrame()
    migrate_db()  # ensure columns exist before querying
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
    # Fill nulls with defaults
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

# ─── INITIAL LOAD ───
df = load_jobs()
total_jobs = len(df)
avg_score = df['score'].mean() if not df.empty else 0
new_count = len(df[df['status'] == 'new']) if not df.empty else 0

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

# ─── SIDEBAR ───
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
        min=0,
        max=100,
        value=0,
        step=5,
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
    html.Div(id="export-download", style={"display": "none"}),
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
                html.Span(f"📊 {total_jobs} jobs", className="me-3"),
                html.Span(f"🆕 {new_count} new", className="me-3"),
                html.Span(f"⭐ {avg_score:.1f} avg", className="me-3"),
                dbc.Button("🌙", id="dark-mode-toggle", color="light", size="sm", className="ms-2"),
            ], className="d-flex align-items-center justify-content-end flex-wrap"),
        ], width="auto", className="ms-auto"),
    ], className="bg-white p-3 rounded-3 shadow-sm mb-4 d-flex align-items-center justify-content-between flex-wrap"),

    # Main content
    dbc.Row([
        dbc.Col(filters_sidebar, xs=12, md=3, lg=2),
        dbc.Col([
            html.Div(id="job-cards-container"),
            html.Div(id="pagination-controls", className="mt-4"),
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

# ─── CALLBACKS ───

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

@app.callback(
    Output("job-cards-container", "children"),
    Output("pagination-controls", "children"),
    Input("status-filter", "value"),
    Input("score-slider", "value"),
    Input("type-filter", "value"),
    Input("date-filter", "value"),
    Input("search-input", "value"),
    Input("sort-filter", "value"),
    Input("refresh-btn", "n_clicks"),
)
def update_jobs(status, min_score, job_type, date_range, search, sort_by, refresh_clicks):
    global df
    if refresh_clicks:
        # Re-run the scraper
        subprocess.run(["python", "scripts/scraper.py"], capture_output=True, text=True)
        df = load_jobs()
    if df.empty:
        return html.Div("No jobs found. Run the scraper."), html.Div()
    
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
        filtered = filtered[
            filtered['title'].str.lower().str.contains(search.lower(), na=False) |
            filtered['company'].str.lower().str.contains(search.lower(), na=False)
        ]
    if sort_by == "score":
        filtered = filtered.sort_values('score', ascending=False)
    elif sort_by == "seen_at":
        filtered = filtered.sort_values('seen_at', ascending=False)
    elif sort_by == "easiest":
        filtered = filtered.sort_values(['type', 'score'], ascending=[True, False])
    
    total = len(filtered)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total > 0 else 1
    
    start = 0
    end = PAGE_SIZE
    page_df = filtered.iloc[start:end]
    
    cards = []
    if not page_df.empty:
        for _, job in page_df.iterrows():
            cards.append(dbc.Col(job_card(job), xs=12, sm=6, lg=4, className="mb-4"))
    else:
        cards = [html.Div("No jobs match your filters.", className="text-center text-muted")]
    
    pagination = html.Div([
        html.Div(f"Showing {start+1}–{min(end, total)} of {total} jobs", className="text-muted me-3"),
        dbc.Pagination(
            id="pagination-component",
            active_page=1,
            max_value=total_pages,
            size="sm",
            className="d-inline-flex"
        ),
    ], className="d-flex align-items-center justify-content-center flex-wrap")
    
    return html.Div(cards, className="row"), pagination

@app.callback(
    Output("job-cards-container", "children", allow_duplicate=True),
    Output("pagination-controls", "children", allow_duplicate=True),
    Input("pagination-component", "active_page"),
    State("status-filter", "value"),
    State("score-slider", "value"),
    State("type-filter", "value"),
    State("date-filter", "value"),
    State("search-input", "value"),
    State("sort-filter", "value"),
    prevent_initial_call=True,
)
def change_page(page, status, min_score, job_type, date_range, search, sort_by):
    global df
    if df.empty:
        return html.Div("No jobs found."), html.Div()
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
        filtered = filtered[
            filtered['title'].str.lower().str.contains(search.lower(), na=False) |
            filtered['company'].str.lower().str.contains(search.lower(), na=False)
        ]
    if sort_by == "score":
        filtered = filtered.sort_values('score', ascending=False)
    elif sort_by == "seen_at":
        filtered = filtered.sort_values('seen_at', ascending=False)
    elif sort_by == "easiest":
        filtered = filtered.sort_values(['type', 'score'], ascending=[True, False])
    
    total = len(filtered)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    page_df = filtered.iloc[start:end]
    
    cards = []
    if not page_df.empty:
        for _, job in page_df.iterrows():
            cards.append(dbc.Col(job_card(job), xs=12, sm=6, lg=4, className="mb-4"))
    else:
        cards = [html.Div("No jobs on this page.")]
    
    pagination = html.Div([
        html.Div(f"Showing {start+1}–{end} of {total} jobs", className="text-muted me-3"),
        dbc.Pagination(
            id="pagination-component",
            active_page=page,
            max_value=total_pages,
            size="sm",
            className="d-inline-flex"
        ),
    ], className="d-flex align-items-center justify-content-center flex-wrap")
    
    return html.Div(cards, className="row"), pagination

@app.callback(
    Output("ai-blog-content", "children"),
    Input("generate-blog-btn", "n_clicks"),
    prevent_initial_call=True,
)
def generate_blog(n):
    if not n:
        return "Click 'Generate Blog' to see insights."
    try:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "⚠️ GEMINI_API_KEY not set."
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-pro')
        top_companies = df['company'].value_counts().head(5).to_dict() if not df.empty else {}
        top_roles = df['title'].value_counts().head(5).to_dict() if not df.empty else {}
        total = len(df)
        prompt = f"Write a short blog (200-300 words) about remote job market trends based on: total jobs {total}, top companies {top_companies}, top roles {top_roles}. Use markdown."
        response = model.generate_content(prompt)
        return dcc.Markdown(response.text)
    except Exception as e:
        return f"⚠️ AI blog unavailable: {e}"

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
    except:
        return html.Div("Error loading archived jobs.")

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

@app.callback(
    Output("export-download", "children"),
    Input("export-btn", "n_clicks"),
    prevent_initial_call=True,
)
def export_csv(n):
    if not n:
        return no_update
    if df.empty:
        return html.Div("No data to export.")
    csv_string = df.to_csv(index=False)
    return dcc.Download(
        id="download-csv",
        data=dict(content=csv_string, filename=f"remote_jobs_{datetime.now().strftime('%Y%m%d')}.csv")
    )

if __name__ == "__main__":
    app.run_server(debug=True, host="0.0.0.0", port=8050)
