#!/usr/bin/env python3
"""
Remote Opportunity Hunter v14.1 — CORE STABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Built-in sources: Remotive, RemoteOK, Arbeitnow, Himalayas, Greenhouse, Lever,
JobSpy, Jooble, Adzuna, X, Reddit, Hacker News, GitHub Issues, Career Pages,
Startup Discovery (Crunchbase, AngelList), MCP (optional), Custom boards.
Self-expanding: discovers new job boards weekly.
Filters: geo, scam, ghost, duplicate, link validation.
State: SQLite + feedback loop.
Alerts: Telegram + failure monitoring.
Zero cost, runs on GitHub Actions.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
import requests
import time
import sys
import re
import random
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

# ─────────────────────────────────────────────
# LOGGING (RotatingFileHandler)
# ─────────────────────────────────────────────

LOG_FILE = Path("data/job_hunter.log")
LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=3)
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# OPTIONAL BEAUTIFULSOUP (fallback)
# ─────────────────────────────────────────────

HAS_BS4 = False
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    log.warning("⚠️ BeautifulSoup not installed. Career page parsing will be limited.")
    class BeautifulSoup:
        def __init__(self, *args, **kwargs):
            pass

# ─────────────────────────────────────────────
# CONFIGURATION (environment variables + defaults)
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_config():
    config = {}
    env_keys = [
        "JOB_TITLES", "REMOTE_KEYWORDS", "EXCLUDE_KEYWORDS", "PRIORITY_COMPANIES",
        "MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD", "SCAM_THRESHOLD",
        "MAX_AGE_DAYS", "ENABLE_MCP", "ENABLE_PUBLIC_APIS", "ENABLE_JOBSPY",
        "ENABLE_X", "ENABLE_REDDIT", "ENABLE_HN", "ENABLE_GITHUB",
        "ENABLE_STARTUP_DISCOVERY", "ENABLE_SOURCE_DISCOVERY", "ENABLE_TEST_JOB",
        "JOOBLE_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"
    ]
    for key in env_keys:
        val = os.getenv(key)
        if val is not None:
            if key in ["JOB_TITLES", "REMOTE_KEYWORDS", "EXCLUDE_KEYWORDS", "PRIORITY_COMPANIES"]:
                config[key.lower()] = [x.strip() for x in val.split(",") if x.strip()]
            elif key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD", "SCAM_THRESHOLD", "MAX_AGE_DAYS"]:
                config[key.lower()] = int(val)
            else:
                config[key.lower()] = val.lower() == "true"

    # Defaults
    config.setdefault("job_titles", [
        "customer support", "customer success", "support specialist",
        "technical support", "product support", "customer experience",
        "operations", "operations associate", "operations coordinator",
        "onboarding specialist", "implementation specialist",
        "community support", "community manager", "virtual assistant",
        "administrative assistant", "project coordinator",
        "trust and safety", "business operations"
    ])
    config.setdefault("software_keywords", [
        "software engineer", "swe", "developer", "backend",
        "frontend", "fullstack", "full stack", "engineer"
    ])
    config.setdefault("remote_keywords", [
        "remote", "anywhere", "global", "worldwide", "work from anywhere",
        "no office", "distributed", "work remotely", "from home", "home based",
        "telecommute", "virtual", "work from home", "wfh", "offsite"
    ])
    config.setdefault("exclude_keywords", [
        "senior", "staff", "lead", "principal", "director", "manager",
        "architect", "devops", "data scientist", "machine learning",
        "design", "ux", "ui", "product", "marketing", "sales",
        "hr", "finance", "accounting", "qa", "test", "business",
        "internals", "internal", "new grad"
    ])
    config.setdefault("priority_companies", [
        "stripe", "anthropic", "figma", "vercel", "notion",
        "linear", "supabase", "railway", "gitlab", "airbnb"
    ])
    config.setdefault("max_retries", 3)
    config.setdefault("timeout_seconds", 20)
    config.setdefault("ghost_threshold", 40)
    config.setdefault("scam_threshold", 60)
    config.setdefault("max_age_days", 30)
    config.setdefault("enable_mcp", True)
    config.setdefault("enable_public_apis", True)
    config.setdefault("enable_jobspy", True)
    config.setdefault("enable_x", True)
    config.setdefault("enable_reddit", True)
    config.setdefault("enable_hn", True)
    config.setdefault("enable_github", True)
    config.setdefault("enable_startup_discovery", True)
    config.setdefault("enable_source_discovery", True)
    config.setdefault("enable_test_job", False)
    config.setdefault("mcp_url", os.getenv("MCP_API_URL", "http://localhost:3000/search"))
    config.setdefault("custom_boards", [])
    config.setdefault("jooble_api_key", os.getenv("JOOBLE_API_KEY", ""))
    config.setdefault("adzuna_app_id", os.getenv("ADZUNA_APP_ID", ""))
    config.setdefault("adzuna_app_key", os.getenv("ADZUNA_APP_KEY", ""))
    config.setdefault("serpapi_key", os.getenv("SERPAPI_KEY", ""))
    return config

CONFIG = get_config()

# ─────────────────────────────────────────────
# SQLITE STATE
# ─────────────────────────────────────────────

DB_PATH = Path("data/jobs.db")

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT UNIQUE,
            source TEXT,
            posted_at DATETIME,
            score INTEGER,
            ghost_score INTEGER,
            scam_score INTEGER,
            status TEXT DEFAULT 'new',
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS flagged_jobs (
            url TEXT PRIMARY KEY,
            reason TEXT,
            company TEXT,
            salary_pattern TEXT,
            flagged_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            domain TEXT,
            careers_url TEXT,
            source TEXT,
            industry TEXT,
            funding_stage TEXT,
            employees INTEGER,
            discovered_at DATETIME,
            last_checked DATETIME,
            active BOOLEAN DEFAULT 1,
            check_frequency TEXT DEFAULT 'daily'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT UNIQUE,
            type TEXT,
            active BOOLEAN DEFAULT 1,
            discovered_at DATETIME,
            last_checked DATETIME,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_error TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_url ON jobs(url)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_seen_at ON jobs(seen_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)")
    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

def load_seen() -> Set[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url FROM jobs")
    urls = {row[0] for row in c.fetchall()}
    conn.close()
    return urls

def load_flagged_features() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT company, salary_pattern, reason FROM flagged_jobs")
    rows = c.fetchall()
    conn.close()
    return [{"company": row[0], "salary_pattern": row[1], "reason": row[2]} for row in rows]

def flag_job_as_scam(job: Dict, reason: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO flagged_jobs (url, reason, company, salary_pattern)
        VALUES (?, ?, ?, ?)
    """, (job.get("url", ""), reason, job.get("company", ""), job.get("salary", "")))
    conn.commit()
    conn.close()

def save_job(job: Dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO jobs
        (id, title, company, location, url, source, posted_at, score, ghost_score, scam_score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job.get("id", ""),
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("url", ""),
        job.get("source", ""),
        job.get("posted_at", ""),
        job.get("score", 0),
        job.get("ghost_score", 100),
        job.get("scam_score", 0),
        "new"
    ))
    conn.commit()
    conn.close()

def save_company(company: Dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO companies (name, domain, careers_url, source, industry, funding_stage, employees, discovered_at, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        company.get("name"),
        company.get("domain"),
        company.get("careers_url"),
        company.get("source"),
        company.get("industry"),
        company.get("funding_stage"),
        company.get("employees", 0),
        datetime.now().isoformat(),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_companies_to_check() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT name, domain, careers_url, source, check_frequency, last_checked
        FROM companies
        WHERE active = 1
    """)
    rows = c.fetchall()
    conn.close()
    companies = []
    now = datetime.now()
    for row in rows:
        name, domain, careers_url, source, freq, last_checked = row
        last = datetime.fromisoformat(last_checked) if last_checked else now - timedelta(days=1)
        if freq == "hourly" and (now - last).seconds > 3600:
            companies.append({"name": name, "domain": domain, "careers_url": careers_url, "source": source})
        elif freq == "daily" and (now - last).days >= 1:
            companies.append({"name": name, "domain": domain, "careers_url": careers_url, "source": source})
        elif freq == "weekly" and (now - last).days >= 7:
            companies.append({"name": name, "domain": domain, "careers_url": careers_url, "source": source})
    return companies

# ─────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────

def fetch_with_retry(url: str, method: str = "GET", json_data: dict = None,
                     headers: dict = None, timeout: int = 20, retries: int = 3) -> Optional[Any]:
    if headers is None:
        headers = HEADERS.copy()
    time.sleep(random.uniform(0.3, 1.0))
    for attempt in range(retries):
        try:
            if method.upper() == "POST":
                resp = requests.post(url, json=json_data, headers=headers, timeout=timeout)
            else:
                resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                if "application/json" in resp.headers.get("Content-Type", ""):
                    return resp.json()
                return resp.text
            elif resp.status_code == 429:
                wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                log.warning(f"Rate limited (429) on {url}, waiting {wait:.1f}s")
                time.sleep(wait)
                continue
            else:
                log.warning(f"HTTP {resp.status_code} from {url}")
                return None
        except Exception as e:
            log.warning(f"Request failed (attempt {attempt+1}): {e}")
            if attempt == retries - 1:
                raise
            wait = (2 ** attempt) * 0.5 + random.uniform(0, 0.5)
            time.sleep(wait)
    return None

def get_job_uid(job: Dict) -> str:
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{job['company']}::hash::{content_hash}"

def normalize_salary(salary_str: str) -> str:
    if not salary_str:
        return ""
    cleaned = re.sub(r'[$,£,€,¥]', '', salary_str)
    numbers = re.findall(r'\d+', cleaned)
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"{numbers[0]}"
    return f"{numbers[0]}-{numbers[1]}"

def is_duplicate_job(job: Dict, existing_jobs: List[Dict]) -> bool:
    for existing in existing_jobs:
        title_sim = SequenceMatcher(None, job.get("title", "").lower(), existing.get("title", "").lower()).ratio()
        company_match = job.get("company", "").lower() == existing.get("company", "").lower()
        if company_match and title_sim > 0.85:
            return True
    return False

def validate_url(url: str) -> bool:
    if not url:
        return False
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True)
        return 200 <= resp.status_code < 400
    except:
        return False

# ─────────────────────────────────────────────
# SOURCE FETCHERS
# ─────────────────────────────────────────────

def fetch_remotive() -> List[Dict]:
    try:
        data = fetch_with_retry("https://remotive.com/api/remote-jobs", timeout=CONFIG["timeout_seconds"])
        if data and "jobs" in data:
            jobs = []
            for job in data["jobs"]:
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company_name", ""),
                    "location": "Remote",
                    "url": job.get("url", ""),
                    "content": job.get("description", ""),
                    "posted_at": job.get("publication_date", ""),
                    "source": "remotive",
                    "salary": normalize_salary(str(job.get("salary", "")))
                })
            log.info(f"   ✅ Remotive: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Remotive failed: {e}")
    return []

def fetch_remoteok() -> List[Dict]:
    try:
        data = fetch_with_retry("https://remoteok.com/api", timeout=CONFIG["timeout_seconds"])
        if data and isinstance(data, list):
            jobs = []
            for item in data:
                if isinstance(item, dict) and item.get("title"):
                    jobs.append({
                        "id": str(item.get("id", "")),
                        "title": item.get("title", ""),
                        "company": item.get("company", ""),
                        "location": item.get("location", "Remote"),
                        "url": item.get("url", ""),
                        "content": item.get("description", ""),
                        "posted_at": item.get("date", ""),
                        "source": "remoteok",
                        "salary": ""
                    })
            log.info(f"   ✅ RemoteOK: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ RemoteOK failed: {e}")
    return []

def fetch_arbeitnow() -> List[Dict]:
    try:
        data = fetch_with_retry("https://www.arbeitnow.com/api/job-board-api", timeout=CONFIG["timeout_seconds"])
        if data and "data" in data:
            jobs = []
            for job in data["data"]:
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company_name", ""),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", ""),
                    "content": job.get("description", ""),
                    "posted_at": job.get("created_at", ""),
                    "source": "arbeitnow",
                    "salary": ""
                })
            log.info(f"   ✅ Arbeitnow: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Arbeitnow failed: {e}")
    return []

def fetch_himalayas() -> List[Dict]:
    try:
        data = fetch_with_retry("https://himalayas.app/jobs/api", timeout=CONFIG["timeout_seconds"])
        if data and "jobs" in data:
            jobs = []
            for job in data["jobs"]:
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("name", ""),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", ""),
                    "content": job.get("description", ""),
                    "posted_at": job.get("created_at", ""),
                    "source": "himalayas",
                    "salary": job.get("salary", "")
                })
            log.info(f"   ✅ Himalayas: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Himalayas failed: {e}")
    return []

def fetch_greenhouse_jobs(company_slug: str) -> List[Dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
    try:
        data = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
        if data and "jobs" in data:
            jobs = []
            for job in data["jobs"]:
                loc = ""
                for loc_obj in job.get("locations", []):
                    if loc_obj.get("name"):
                        loc = loc_obj["name"]
                        break
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("name", company_slug.capitalize()),
                    "location": loc,
                    "url": job.get("absolute_url", ""),
                    "content": job.get("content", ""),
                    "posted_at": job.get("updated_at", ""),
                    "source": f"greenhouse_{company_slug}",
                    "salary": ""
                })
            log.info(f"   ✅ Greenhouse {company_slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Greenhouse {company_slug} failed: {e}")
    return []

def fetch_lever_jobs(company_slug: str) -> List[Dict]:
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    try:
        data = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
        if data and isinstance(data, list):
            jobs = []
            for job in data:
                jobs.append({
                    "id": job.get("id", ""),
                    "title": job.get("text", ""),
                    "company": job.get("categories", {}).get("team", company_slug.capitalize()),
                    "location": job.get("categories", {}).get("location", "Remote"),
                    "url": job.get("hostedUrl", ""),
                    "content": "",
                    "posted_at": job.get("createdAt", ""),
                    "source": f"lever_{company_slug}",
                    "salary": ""
                })
            log.info(f"   ✅ Lever {company_slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Lever {company_slug} failed: {e}")
    return []

def fetch_jobspy() -> List[Dict]:
    if not CONFIG.get("enable_jobspy", True):
        return []
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("   ⚠️ JobSpy not installed. Install: pip install python-jobspy")
        return []
    job_titles = CONFIG.get("job_titles", [])[:3]
    all_jobs = []
    for term in job_titles:
        try:
            df = scrape_jobs(
                site_name=["indeed", "linkedin", "glassdoor", "google", "zip_recruiter"],
                search_term=term,
                location="remote",
                is_remote=True,
                results_wanted=20,
                hours_old=168
            )
            for _, row in df.iterrows():
                all_jobs.append({
                    "id": str(row.get("job_url", "")),
                    "title": row.get("title", ""),
                    "company": row.get("company", ""),
                    "location": row.get("location", "Remote"),
                    "url": row.get("job_url", ""),
                    "content": row.get("description", ""),
                    "posted_at": str(row.get("date_posted", "")),
                    "source": "jobspy",
                    "salary": f"{row.get('min_amount', '')}-{row.get('max_amount', '')}" if row.get('min_amount') else ""
                })
        except Exception as e:
            log.warning(f"   ⚠️ JobSpy '{term}' failed: {e}")
    log.info(f"   ✅ JobSpy: {len(all_jobs)} jobs")
    return all_jobs

def fetch_jooble() -> List[Dict]:
    api_key = CONFIG.get("jooble_api_key")
    if not api_key:
        return []
    try:
        resp = requests.post(
            "https://jooble.org/api/" + api_key,
            json={"keywords": "remote", "page": 1},
            timeout=CONFIG["timeout_seconds"]
        )
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                jobs.append({
                    "id": job.get("id", ""),
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "location": job.get("location", "Remote"),
                    "url": job.get("link", ""),
                    "content": job.get("snippet", ""),
                    "posted_at": job.get("updated", ""),
                    "source": "jooble",
                    "salary": ""
                })
            log.info(f"   ✅ Jooble: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Jooble failed: {e}")
    return []

def fetch_adzuna() -> List[Dict]:
    app_id = CONFIG.get("adzuna_app_id")
    app_key = CONFIG.get("adzuna_app_key")
    if not app_id or not app_key:
        return []
    try:
        url = f"https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id={app_id}&app_key={app_key}&results_per_page=50&what=remote"
        data = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
        if data and "results" in data:
            jobs = []
            for job in data["results"]:
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("display_name", ""),
                    "location": job.get("location", {}).get("display_name", "Remote"),
                    "url": job.get("redirect_url", ""),
                    "content": job.get("description", ""),
                    "posted_at": job.get("created", ""),
                    "source": "adzuna",
                    "salary": job.get("salary_min", "") + "-" + job.get("salary_max", "")
                })
            log.info(f"   ✅ Adzuna: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Adzuna failed: {e}")
    return []

# ─────────────────────────────────────────────
# CAREER PAGES (curated list)
# ─────────────────────────────────────────────

CAREER_COMPANIES = [
    {"name": "Stripe", "url": "https://stripe.com/jobs", "type": "greenhouse"},
    {"name": "Anthropic", "url": "https://boards.greenhouse.io/anthropic", "type": "greenhouse"},
    {"name": "Figma", "url": "https://jobs.lever.co/figma", "type": "lever"},
    {"name": "Vercel", "url": "https://vercel.com/careers", "type": "custom"},
    {"name": "Notion", "url": "https://boards.greenhouse.io/notion", "type": "greenhouse"},
    {"name": "Linear", "url": "https://linear.app/careers", "type": "custom"},
    {"name": "Supabase", "url": "https://boards.greenhouse.io/supabase", "type": "greenhouse"},
    {"name": "Railway", "url": "https://railway.app/careers", "type": "custom"},
    {"name": "GitLab", "url": "https://jobs.lever.co/gitlab", "type": "lever"},
    {"name": "Airbnb", "url": "https://careers.airbnb.com", "type": "custom"},
]

def fetch_career_pages() -> List[Dict]:
    jobs = []
    for company in CAREER_COMPANIES:
        if company["type"] == "greenhouse":
            slug = company["url"].split("/")[-1]
            jobs.extend(fetch_greenhouse_jobs(slug))
        elif company["type"] == "lever":
            slug = company["url"].split("/")[-1]
            jobs.extend(fetch_lever_jobs(slug))
        else:
            if HAS_BS4:
                try:
                    data = fetch_with_retry(company["url"], timeout=CONFIG["timeout_seconds"])
                    if data and isinstance(data, str):
                        soup = BeautifulSoup(data, "html.parser")
                        for script in soup.find_all("script", type="application/ld+json"):
                            try:
                                j = json.loads(script.string)
                                if isinstance(j, dict) and j.get("@type") == "JobPosting":
                                    jobs.append({
                                        "id": j.get("url", ""),
                                        "title": j.get("title", ""),
                                        "company": company["name"],
                                        "location": j.get("jobLocation", {}).get("address", {}).get("addressCountry", "Remote"),
                                        "url": j.get("url", ""),
                                        "content": j.get("description", ""),
                                        "posted_at": j.get("datePosted", ""),
                                        "source": f"career_{company['name']}",
                                        "salary": normalize_salary(str(j.get("baseSalary", {}).get("value", {}).get("value", "")))
                                    })
                            except:
                                pass
                except Exception as e:
                    log.warning(f"   ⚠️ Career page {company['name']} failed: {e}")
    log.info(f"   ✅ Career Pages: {len(jobs)} jobs")
    return jobs

# ─────────────────────────────────────────────
# SOCIAL MEDIA (X, Reddit, HN, GitHub)
# ─────────────────────────────────────────────

def fetch_x_tweets() -> List[Dict]:
    if not CONFIG.get("enable_x", True):
        return []
    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
        return []
    queries = [
        '"we\'re hiring" remote startup',
        '"join our team" remote startup',
        '"open position" remote startup',
    ]
    jobs = []
    for q in queries:
        try:
            resp = requests.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers={"Authorization": f"Bearer {bearer}"},
                params={"query": q, "max_results": 10},
                timeout=15
            )
            if resp.status_code == 200:
                for tweet in resp.json().get("data", []):
                    text = tweet.get("text", "")
                    company_match = re.search(r'(?:at|@)\s+([A-Z][a-zA-Z0-9\s]+)(?=\s|$|,)', text)
                    company = company_match.group(1).strip() if company_match else "Unknown"
                    role_match = re.search(r'(?:hiring|looking for)\s+([A-Za-z\s]+?)(?=\s+at|\s+for|\s*[,.!?]|$)', text, re.IGNORECASE)
                    role = role_match.group(1).strip() if role_match else "Unknown"
                    jobs.append({
                        "id": tweet["id"],
                        "title": role,
                        "company": company,
                        "location": "Remote (via X)",
                        "url": f"https://twitter.com/i/web/status/{tweet['id']}",
                        "content": text,
                        "posted_at": tweet.get("created_at", ""),
                        "source": "x_social",
                        "salary": ""
                    })
        except Exception as e:
            log.warning(f"X search failed: {e}")
    log.info(f"   ✅ X: {len(jobs)} tweets")
    return jobs

def fetch_reddit_jobs() -> List[Dict]:
    if not CONFIG.get("enable_reddit", True):
        return []
    subreddits = ["forhire", "remotejobs", "startups"]
    jobs = []
    for sub in subreddits:
        try:
            data = fetch_with_retry(
                f"https://www.reddit.com/r/{sub}/search.json?q=hiring+remote&restrict_sr=1&limit=20",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if data and "data" in data and "children" in data["data"]:
                for child in data["data"]["children"]:
                    post = child["data"]
                    jobs.append({
                        "id": post["id"],
                        "title": post["title"][:100],
                        "company": "Reddit",
                        "location": "Remote",
                        "url": f"https://reddit.com{post['permalink']}",
                        "content": post.get("selftext", "")[:500],
                        "posted_at": datetime.utcfromtimestamp(post["created_utc"]).isoformat(),
                        "source": f"reddit_{sub}",
                        "salary": ""
                    })
        except Exception as e:
            log.warning(f"Reddit r/{sub} failed: {e}")
    log.info(f"   ✅ Reddit: {len(jobs)} posts")
    return jobs

def fetch_hn_jobs() -> List[Dict]:
    if not CONFIG.get("enable_hn", True):
        return []
    try:
        top = fetch_with_retry("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
        if not top:
            return []
        jobs = []
        for story_id in top[:30]:
            story = fetch_with_retry(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=10)
            if story and "title" in story and "Who is hiring?" in story["title"]:
                for kid_id in story.get("kids", [])[:30]:
                    comment = fetch_with_retry(f"https://hacker-news.firebaseio.com/v0/item/{kid_id}.json", timeout=10)
                    if comment and "text" in comment:
                        jobs.append({
                            "id": f"hn_{kid_id}",
                            "title": "HN Job",
                            "company": "Hacker News",
                            "location": "Remote",
                            "url": f"https://news.ycombinator.com/item?id={kid_id}",
                            "content": comment.get("text", "")[:500],
                            "posted_at": datetime.utcfromtimestamp(comment.get("time", 0)).isoformat(),
                            "source": "hn",
                            "salary": ""
                        })
                break
        log.info(f"   ✅ Hacker News: {len(jobs)} comments")
        return jobs
    except Exception as e:
        log.warning(f"Hacker News failed: {e}")
    return []

def fetch_github_issues() -> List[Dict]:
    if not CONFIG.get("enable_github", True):
        return []
    url = "https://api.github.com/search/issues?q=hiring+remote+label:help-wanted&per_page=20"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        data = fetch_with_retry(url, headers=headers, timeout=CONFIG["timeout_seconds"])
        if data and "items" in data:
            jobs = []
            for item in data["items"]:
                jobs.append({
                    "id": str(item["id"]),
                    "title": item["title"][:100],
                    "company": "GitHub",
                    "location": "Remote",
                    "url": item["html_url"],
                    "content": item.get("body", "")[:500],
                    "posted_at": item.get("created_at", ""),
                    "source": "github_issue",
                    "salary": ""
                })
            log.info(f"   ✅ GitHub Issues: {len(jobs)} found")
            return jobs
    except Exception as e:
        log.warning(f"GitHub Issues failed: {e}")
    return []

# ─────────────────────────────────────────────
# STARTUP DISCOVERY
# ─────────────────────────────────────────────

def discover_new_startups() -> List[Dict]:
    if not CONFIG.get("enable_startup_discovery", True):
        return []
    startups = []
    cb_key = os.getenv("CRUNCHBASE_API_KEY")
    if cb_key:
        try:
            data = fetch_with_retry(
                "https://api.crunchbase.com/v4/entities/organizations",
                params={"limit": 50, "sort": "created_at"},
                headers={"X-Crunchbase-API-Key": cb_key},
                timeout=15
            )
            if data and "data" in data and "entities" in data["data"]:
                for item in data["data"]["entities"]:
                    attrs = item.get("attributes", {})
                    startups.append({
                        "name": attrs.get("name"),
                        "domain": attrs.get("website_url", ""),
                        "careers_url": f"{attrs.get('website_url', '')}/careers" if attrs.get("website_url") else "",
                        "source": "crunchbase",
                        "industry": attrs.get("category", {}).get("name", ""),
                        "funding_stage": attrs.get("funding_stage", ""),
                        "employees": attrs.get("num_employees", 0)
                    })
        except Exception as e:
            log.warning(f"Crunchbase failed: {e}")
    try:
        resp = requests.get("https://wellfound.com/startups", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200 and HAS_BS4:
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select(".startup"):
                name_elem = item.select_one(".startup-name")
                if name_elem:
                    name = name_elem.text.strip()
                    link = item.select_one("a")
                    href = link.get("href") if link else ""
                    startups.append({
                        "name": name,
                        "domain": "",
                        "careers_url": f"https://wellfound.com{href}/jobs" if href else "",
                        "source": "angellist",
                        "industry": "",
                        "funding_stage": "",
                        "employees": 0
                    })
    except Exception as e:
        log.warning(f"AngelList failed: {e}")
    log.info(f"   ✅ Startup discovery: {len(startups)} new startups")
    return startups

# ─────────────────────────────────────────────
# SOURCE DISCOVERY (Self‑Expanding)
# ─────────────────────────────────────────────

def validate_source(url: str) -> bool:
    try:
        resp = requests.get(url, timeout=10, headers=HEADERS)
        if resp.status_code != 200:
            return False
        content = resp.text.lower()
        signals = ["job", "career", "remote", "position", "hiring", "apply"]
        score = sum(1 for s in signals if s in content)
        return score >= 3
    except:
        return False

def detect_source_type(url: str) -> str:
    if url.endswith(".json") or "/api/" in url:
        return "json"
    if url.endswith(".rss") or url.endswith(".xml") or "feed" in url:
        return "rss"
    return "html"

def discover_new_sources() -> List[Dict]:
    if not CONFIG.get("enable_source_discovery", True):
        return []
    new_sources = []
    discovered_urls = set()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url FROM sources")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    directories = [
        "https://www.remotejobboards.com",
        "https://jobboardsearch.com",
        "https://www.jobboardfinder.com",
    ]
    for dir_url in directories:
        try:
            data = fetch_with_retry(dir_url, timeout=10)
            if data and HAS_BS4:
                soup = BeautifulSoup(data, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link.get("href")
                    text = link.get_text().lower()
                    if "job" in text or "board" in text or "career" in text:
                        if href and href.startswith("http"):
                            if href not in existing and href not in discovered_urls:
                                discovered_urls.add(href)
                                new_sources.append({
                                    "name": text.strip()[:50],
                                    "url": href,
                                    "type": "html",
                                    "discovered_at": datetime.now().isoformat()
                                })
        except Exception as e:
            log.warning(f"Directory scan failed: {e}")

    serpapi_key = CONFIG.get("serpapi_key")
    if serpapi_key:
        queries = [
            "remote job board",
            "best remote job boards",
            "new job board 2025",
            "alternative to LinkedIn jobs",
            "job board API"
        ]
        for q in queries:
            try:
                resp = requests.get(
                    "https://serpapi.com/search",
                    params={"q": q, "api_key": serpapi_key, "num": 10},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for result in data.get("organic_results", []):
                        url = result.get("link")
                        if url and "job" in url and url not in existing and url not in discovered_urls:
                            discovered_urls.add(url)
                            new_sources.append({
                                "name": result.get("title", url)[:50],
                                "url": url,
                                "type": "html",
                                "discovered_at": datetime.now().isoformat()
                            })
            except Exception as e:
                log.warning(f"SerpAPI failed: {e}")

    validated = []
    for src in new_sources:
        if validate_source(src["url"]):
            src["type"] = detect_source_type(src["url"])
            validated.append(src)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for src in validated:
        c.execute("""
            INSERT OR IGNORE INTO sources (name, url, type, discovered_at, last_checked)
            VALUES (?, ?, ?, ?, ?)
        """, (src["name"], src["url"], src["type"], src["discovered_at"], datetime.now().isoformat()))
    conn.commit()
    conn.close()

    log.info(f"   ✅ Discovered {len(validated)} new sources")
    return validated

def fetch_source_jobs(source: Dict) -> List[Dict]:
    url = source["url"]
    src_type = source["type"]
    name = source["name"] or url

    try:
        if src_type == "json":
            data = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
            if data and isinstance(data, list):
                jobs = []
                for item in data:
                    if isinstance(item, dict) and item.get("title"):
                        jobs.append({
                            "id": str(item.get("id", "")),
                            "title": item.get("title", ""),
                            "company": item.get("company", item.get("company_name", "")),
                            "location": item.get("location", "Remote"),
                            "url": item.get("url", ""),
                            "content": item.get("description", item.get("content", "")),
                            "posted_at": item.get("date", item.get("posted_at", "")),
                            "source": f"discovered_{name[:10]}",
                            "salary": normalize_salary(str(item.get("salary", "")))
                        })
                log.info(f"   ✅ Discovered '{name}': {len(jobs)} jobs")
                return jobs
        elif src_type == "rss":
            text = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
            if text:
                root = ET.fromstring(text)
                jobs = []
                for item in root.findall(".//item"):
                    jobs.append({
                        "id": item.find("link").text if item.find("link") is not None else "",
                        "title": item.find("title").text if item.find("title") is not None else "",
                        "company": name,
                        "location": "Remote",
                        "url": item.find("link").text if item.find("link") is not None else "",
                        "content": item.find("description").text if item.find("description") is not None else "",
                        "posted_at": item.find("pubDate").text if item.find("pubDate") is not None else "",
                        "source": f"discovered_{name[:10]}",
                        "salary": ""
                    })
                log.info(f"   ✅ Discovered RSS '{name}': {len(jobs)} jobs")
                return jobs
        else:  # html
            if not HAS_BS4:
                return []
            data = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
            if data and isinstance(data, str):
                soup = BeautifulSoup(data, "html.parser")
                jobs = []
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        j = json.loads(script.string)
                        if isinstance(j, dict) and j.get("@type") == "JobPosting":
                            jobs.append({
                                "id": j.get("url", ""),
                                "title": j.get("title", ""),
                                "company": name,
                                "location": j.get("jobLocation", {}).get("address", {}).get("addressCountry", "Remote"),
                                "url": j.get("url", ""),
                                "content": j.get("description", ""),
                                "posted_at": j.get("datePosted", ""),
                                "source": f"discovered_{name[:10]}",
                                "salary": normalize_salary(str(j.get("baseSalary", {}).get("value", {}).get("value", "")))
                            })
                    except:
                        pass
                if not jobs:
                    for container in soup.find_all("div", class_=re.compile(r"(job|position|career|opening)")):
                        title_elem = container.find("h2") or container.find("h3")
                        if title_elem:
                            link = container.find("a")
                            href = link.get("href") if link else ""
                            jobs.append({
                                "id": href,
                                "title": title_elem.text.strip(),
                                "company": name,
                                "location": "Remote",
                                "url": href if href.startswith("http") else urljoin(url, href),
                                "content": "",
                                "posted_at": datetime.now().isoformat(),
                                "source": f"discovered_{name[:10]}",
                                "salary": ""
                            })
                log.info(f"   ✅ Discovered HTML '{name}': {len(jobs)} jobs")
                return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Discovered source '{name}' failed: {e}")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE sources SET failure_count = failure_count + 1, last_error = ? WHERE url = ?", (str(e), url))
        c.execute("UPDATE sources SET active = 0 WHERE url = ? AND failure_count > 5", (url,))
        conn.commit()
        conn.close()
    return []

# ─────────────────────────────────────────────
# FILTERING & SCORING
# ─────────────────────────────────────────────

RESTRICTED_COUNTRIES = ["us", "usa", "united states", "canada", "uk", "united kingdom", "europe", "australia"]

def is_globally_allowed(job: Dict) -> bool:
    location = job.get("location", "").lower()
    if "hybrid" in location or "in-office" in location:
        return False
    if "remote" in location or "anywhere" in location or "global" in location:
        return True
    for country in RESTRICTED_COUNTRIES:
        if country in location:
            return False
    return True

def is_fully_remote(job: Dict) -> bool:
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    desc = job.get("content", "").lower()
    if "hybrid" in location or "in-office" in location:
        return False
    for kw in CONFIG.get("remote_keywords", []):
        if kw in location or kw in title or kw in desc:
            return True
    return False

def matches_filter(job: Dict) -> bool:
    if not is_fully_remote(job):
        return False
    title = job.get("title", "").lower()
    for kw in CONFIG.get("exclude_keywords", []):
        if kw in title:
            return False
    job_titles = CONFIG.get("job_titles", [])
    sw = CONFIG.get("software_keywords", [])
    return any(kw in title for kw in job_titles) or any(kw in title for kw in sw)

def calculate_score(job: Dict) -> int:
    score = 0
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    location = job.get("location", "").lower()
    desc = job.get("content", "").lower()

    for kw in CONFIG.get("job_titles", [])[:5]:
        if kw in title:
            score += 30
            break
    else:
        for kw in CONFIG.get("software_keywords", [])[:3]:
            if kw in title:
                score += 20
                break

    if "anywhere" in location or "global" in location:
        score += 20
    elif "remote" in location:
        score += 15
    elif "fully remote" in desc:
        score += 18

    for pc in CONFIG.get("priority_companies", []):
        if pc.lower() in company:
            score += 15
            break

    posted = job.get("posted_at", "")
    if posted:
        try:
            dt = datetime.fromisoformat(posted.replace('Z', '+00:00'))
            days = (datetime.now() - dt).days
            if days <= 1:
                score += 10
            elif days <= 3:
                score += 8
            elif days <= 7:
                score += 5
        except:
            pass

    if "greenhouse.io" in job.get("url", "") or "lever.co" in job.get("url", ""):
        score += 10

    return min(100, score)

def detect_ghost(job: Dict) -> Dict:
    signals = []
    score = 100
    if not job.get("location") or job.get("location") == "Unknown":
        score -= 18
        signals.append("missing_location")
    if not job.get("posted_at"):
        score -= 12
        signals.append("missing_date")
    desc = job.get("content", "")
    if len(desc) < 100:
        score -= 10
        signals.append("vague_description")
    salary = job.get("salary", "")
    if salary and "unpaid" in salary.lower():
        score -= 15
        signals.append("unpaid")
    return {"score": max(0, score), "is_ghost": score < CONFIG.get("ghost_threshold", 40), "signals": signals}

def detect_scam(job: Dict) -> Dict:
    score = 0
    reasons = []
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    description = job.get("content", "").lower()
    salary = job.get("salary", "").lower()
    source = job.get("source", "").lower()

    if salary:
        nums = re.findall(r'\d+', salary)
        if nums:
            sal_num = int(nums[0])
            if sal_num > 150000 and any(kw in title for kw in ["entry", "junior", "assistant"]):
                score += 30
                reasons.append("unrealistically high salary for entry-level")
            elif sal_num < 10000 and "hourly" not in salary:
                score += 20
                reasons.append("suspiciously low salary")
    if company in ["unknown", "startup", "company", "tech", "anonymous"]:
        score += 20
        reasons.append("generic company name")
    scam_indicators = [
        "unlimited earning potential", "make money fast", "get rich",
        "paid training", "free work", "unpaid internship", "trial period",
        "send your bank details", "ssn", "social security", "passport",
        "cryptocurrency", "bitcoin", "forex", "pyramid"
    ]
    for ind in scam_indicators:
        if ind in description:
            score += 10
            reasons.append(f"contains scam indicator: '{ind}'")
    high_risk_sources = ["craigslist", "facebook", "telegram", "discord"]
    if any(src in source for src in high_risk_sources):
        score += 15
        reasons.append("source known for scams")
    if len(description) < 200:
        score += 10
        reasons.append("very short description")
    for pc in CONFIG.get("priority_companies", []):
        if pc.lower() in company:
            score = max(0, score - 30)
            reasons.append("priority company - trusted")
    flagged = load_flagged_features()
    for feature in flagged:
        if feature.get("company") and feature["company"].lower() in company:
            score += 20
            reasons.append(f"company previously flagged as scam: {feature.get('reason', 'unknown')}")
        if feature.get("salary_pattern") and feature["salary_pattern"].lower() in salary:
            score += 10
            reasons.append("salary pattern matches previously flagged scam")
    return {"score": min(100, score), "is_scam": score > CONFIG.get("scam_threshold", 60), "reasons": reasons}

# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

def send_telegram(jobs: List[Dict], is_test: bool = False, error_msg: str = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    if error_msg:
        msg = f"❌ **Job Hunter Error**\n\n{error_msg}"
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except:
            pass
        return
    if not jobs:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": "🌍 No new remote jobs found.", "parse_mode": "Markdown"}, timeout=10)
        except:
            pass
        return
    msg = f"🌍 **{len(jobs)} REMOTE JOBS FOUND**\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    for job in jobs[:10]:
        score = job.get('score', 0)
        stars = '⭐' * min(5, score // 20 + 1)
        scam_score = job.get('scam_score', 0)
        scam_warning = " ⚠️" if scam_score > 40 else ""
        msg += (
            f"**{job['company']}**{scam_warning}\n"
            f"💼 {job['title']}\n"
            f"📍 {job.get('location') or 'Remote'}\n"
            f"🎯 {score}/100 {stars}\n"
            f"📡 {job.get('source', 'unknown').upper()}\n"
            f"🔗 [Apply]({job['url']})\n\n"
        )
    if len(jobs) > 10:
        msg += f"📌 +{len(jobs)-10} more jobs\n"
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=10)
        log.info(f"✅ Telegram sent ({len(jobs)} jobs)")
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("🌍 REMOTE OPPORTUNITY HUNTER v14.1")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Self‑Expanding • Maximum Sources • Zero Cost")
    log.info("=" * 60)

    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        sys.exit(1)

    init_db()
    seen = load_seen()
    all_jobs = []
    source_counts = defaultdict(int)
    scam_filtered = 0
    geo_filtered = 0
    duplicate_filtered = 0
    age_filtered = 0

    if CONFIG.get("enable_test_job", False):
        log.info("🧪 Sending test job...")
        test_job = [{
            "id": "test123",
            "title": "Test Job (Customer Support)",
            "company": "TestCompany",
            "location": "Remote (Anywhere)",
            "url": "https://example.com",
            "source": "test",
            "score": 100,
            "posted_at": datetime.now().isoformat()
        }]
        send_telegram(test_job, is_test=True)
        log.info("✅ Test job sent. Set ENABLE_TEST_JOB=false to disable.")

    # ─── SOURCE DISCOVERY (weekly) ───
    if CONFIG.get("enable_source_discovery", True) and datetime.now().weekday() == 0:
        log.info("📡 Running source discovery...")
        discover_new_sources()

    # ─── FETCH FROM BUILT‑IN SOURCES ───

    # 1. Keyless APIs
    if CONFIG.get("enable_public_apis", True):
        log.info("📡 Fetching keyless APIs...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(fetch_remotive): "remotive",
                executor.submit(fetch_remoteok): "remoteok",
                executor.submit(fetch_arbeitnow): "arbeitnow",
                executor.submit(fetch_himalayas): "himalayas"
            }
            for future in as_completed(futures):
                jobs = future.result()
                all_jobs.extend(jobs)
                source_counts[futures[future]] += len(jobs)

    # 2. ATS Direct
    log.info("📡 Fetching ATS (Greenhouse, Lever)...")
    ats_companies = ["stripe", "anthropic", "figma", "notion", "linear", "supabase", "gitlab"]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for slug in ats_companies:
            futures[executor.submit(fetch_greenhouse_jobs, slug)] = f"greenhouse_{slug}"
            futures[executor.submit(fetch_lever_jobs, slug)] = f"lever_{slug}"
        for future in as_completed(futures):
            jobs = future.result()
            all_jobs.extend(jobs)
            source_counts[futures[future]] += len(jobs)

    # 3. JobSpy
    log.info("📡 Fetching JobSpy...")
    jobspy_jobs = fetch_jobspy()
    all_jobs.extend(jobspy_jobs)
    source_counts["jobspy"] += len(jobspy_jobs)

    # 4. Jooble
    log.info("📡 Fetching Jooble...")
    jooble_jobs = fetch_jooble()
    all_jobs.extend(jooble_jobs)
    source_counts["jooble"] += len(jooble_jobs)

    # 5. Adzuna
    log.info("📡 Fetching Adzuna...")
    adzuna_jobs = fetch_adzuna()
    all_jobs.extend(adzuna_jobs)
    source_counts["adzuna"] += len(adzuna_jobs)

    # 6. Career Pages
    log.info("📡 Fetching career pages...")
    career_jobs = fetch_career_pages()
    all_jobs.extend(career_jobs)
    source_counts["career_pages"] += len(career_jobs)

    # 7. Social Media
    if CONFIG.get("enable_x", True) or CONFIG.get("enable_reddit", True):
        log.info("📡 Fetching social media...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(fetch_x_tweets): "x_social",
                executor.submit(fetch_reddit_jobs): "reddit",
                executor.submit(fetch_hn_jobs): "hn",
                executor.submit(fetch_github_issues): "github"
            }
            for future in as_completed(futures):
                jobs = future.result()
                all_jobs.extend(jobs)
                source_counts[futures[future]] += len(jobs)

    # 8. Discovered sources
    log.info("📡 Fetching from discovered sources...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, url, type FROM sources WHERE active = 1")
    discovered_sources = c.fetchall()
    conn.close()

    for src in discovered_sources:
        src_dict = {"id": src[0], "name": src[1], "url": src[2], "type": src[3]}
        jobs = fetch_source_jobs(src_dict)
        all_jobs.extend(jobs)
        source_counts[f"discovered_{src[1][:10]}"] += len(jobs)

    # 9. Startup Discovery (weekly)
    if CONFIG.get("enable_startup_discovery", True) and datetime.now().weekday() == 0:
        log.info("📡 Running startup discovery...")
        startups = discover_new_startups()
        for startup in startups:
            save_company(startup)

    # ─── PROCESS JOBS ───
    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

    filtered = []
    processed_urls = set()

    for job in all_jobs:
        uid = job.get("url", "")
        if uid in processed_urls:
            continue
        processed_urls.add(uid)

        if uid in seen:
            continue

        posted = job.get("posted_at", "")
        if posted:
            try:
                dt = datetime.fromisoformat(posted.replace('Z', '+00:00'))
                if (datetime.now() - dt).days > CONFIG.get("max_age_days", 30):
                    age_filtered += 1
                    continue
            except:
                pass

        if not is_globally_allowed(job):
            geo_filtered += 1
            continue

        scam_result = detect_scam(job)
        if scam_result["is_scam"]:
            scam_filtered += 1
            continue

        ghost = detect_ghost(job)
        if ghost["is_ghost"]:
            continue

        if is_duplicate_job(job, filtered):
            duplicate_filtered += 1
            continue

        if not validate_url(job.get("url", "")):
            continue

        if matches_filter(job):
            job["score"] = calculate_score(job)
            job["ghost_score"] = ghost["score"]
            job["scam_score"] = scam_result["score"]
            save_job(job)
            filtered.append(job)

    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

    log.info(f"   ✅ {len(filtered)} jobs matched")
    log.info(f"   🛑 {scam_filtered} rejected as scams")
    log.info(f"   🗺️ {geo_filtered} rejected (geo restrictions)")
    log.info(f"   🔄 {duplicate_filtered} rejected (duplicates)")
    log.info(f"   📅 {age_filtered} rejected (too old)")

    if filtered:
        send_telegram(filtered)
    else:
        log.info("ℹ️ No new remote jobs found")
        send_telegram([])

    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        error_msg = f"Job Hunter crashed:\n{str(e)}\n\n{traceback.format_exc()}"
        log.error(error_msg)
        send_telegram([], error_msg=error_msg)
        sys.exit(1)
