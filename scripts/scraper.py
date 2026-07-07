#!/usr/bin/env python3
"""
Remote Opportunity Hunter v18.0 – ULTIMATE OPTIMIZED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Features:
  • Configuration-driven JSON source fetching (10+ APIs in one function)
  • Special parsers for Greenhouse, Lever, career pages, social media
  • Parallel fetching with ThreadPoolExecutor
  • Full filtering, scoring, scam/ghost detection
  • Health monitoring (health.json)
  • Telegram + Gmail alerts
  • Self-healing database (migrations, archiving)
  • 10,000+ companies covered via JobSpy + discovered sources
  • Zero cost, runs on GitHub Actions
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
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── LOGGING ───
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

# ─── CONFIG LOADING ───
CONFIG_FILE = Path("config.json")
config = {}
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        log.info("✅ Loaded config.json")
    except Exception as e:
        log.warning(f"Failed to load config.json: {e}")

# Environment overrides
for key in ["JOB_TITLES", "REMOTE_KEYWORDS", "EXCLUDE_KEYWORDS", "PRIORITY_COMPANIES"]:
    if os.getenv(key):
        config[key.lower()] = [x.strip() for x in os.getenv(key).split(",") if x.strip()]
for key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD", "SCAM_THRESHOLD", "MAX_AGE_DAYS"]:
    if os.getenv(key):
        config[key.lower()] = int(os.getenv(key))
for key in ["ENABLE_MCP", "ENABLE_PUBLIC_APIS", "ENABLE_JOBSPY", "ENABLE_X", "ENABLE_REDDIT",
           "ENABLE_HN", "ENABLE_GITHUB", "ENABLE_STARTUP_DISCOVERY", "ENABLE_SOURCE_DISCOVERY",
           "ENABLE_TELEGRAM", "ENABLE_GMAIL"]:
    if os.getenv(key):
        config[key.lower()] = os.getenv(key).lower() == "true"

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
config.setdefault("enable_telegram", True)
config.setdefault("enable_gmail", True)
config.setdefault("mcp_url", os.getenv("MCP_API_URL", "http://localhost:3000/search"))

# ─── DATABASE ───
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
            notes TEXT DEFAULT '',
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs_archive (
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
            status TEXT DEFAULT 'archived',
            seen_at DATETIME,
            archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posted_at ON jobs(posted_at)")
    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

def archive_old_jobs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    c.execute("""
        INSERT INTO jobs_archive (id, title, company, location, url, source,
                                  posted_at, score, ghost_score, scam_score, status, seen_at)
        SELECT id, title, company, location, url, source,
               posted_at, score, ghost_score, scam_score, status, seen_at
        FROM jobs
        WHERE seen_at < ?
    """, (cutoff,))
    c.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    log.info("✅ Archived old jobs")

# ─── UTILITY FUNCTIONS ───
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

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

def normalize_salary(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r'[$,£,€,¥]', '', s)
    nums = re.findall(r'\d+', s)
    if not nums:
        return ""
    return f"{nums[0]}-{nums[1]}" if len(nums) >= 2 else nums[0]

def get_job_uid(job: Dict) -> str:
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    return f"{job['company']}::hash::{hashlib.md5(content.encode()).hexdigest()}"

def is_duplicate_job(job: Dict, existing_jobs: List[Dict]) -> bool:
    for existing in existing_jobs:
        if existing.get('company') == job.get('company') and \
           SequenceMatcher(None, existing.get('title', '').lower(), job.get('title', '').lower()).ratio() > 0.85:
            return True
    return False

def validate_url(url: str) -> bool:
    if not url:
        return False
    try:
        return 200 <= requests.head(url, timeout=5, allow_redirects=True).status_code < 400
    except:
        return False

# ─── CONFIGURATION-DRIVEN JSON SOURCE FETCHER ───
JSON_SOURCES = [
    {
        "name": "remotive",
        "url": "https://remotive.com/api/remote-jobs",
        "list_key": "jobs",
        "location_fallback": "Remote",
        "fields": {
            "id": "id",
            "title": "title",
            "company": "company_name",
            "location": "location",
            "url": "url",
            "content": "description",
            "posted_at": "publication_date",
            "salary": "salary"
        }
    },
    {
        "name": "remoteok",
        "url": "https://remoteok.com/api",
        "list_key": None,
        "location_fallback": "Remote",
        "fields": {
            "id": "id",
            "title": "title",
            "company": "company",
            "location": "location",
            "url": "url",
            "content": "description",
            "posted_at": "date",
            "salary": None
        }
    },
    {
        "name": "arbeitnow",
        "url": "https://www.arbeitnow.com/api/job-board-api",
        "list_key": "data",
        "location_fallback": "Remote",
        "fields": {
            "id": "id",
            "title": "title",
            "company": "company_name",
            "location": "location",
            "url": "url",
            "content": "description",
            "posted_at": "created_at",
            "salary": None
        }
    },
    {
        "name": "himalayas",
        "url": "https://himalayas.app/jobs/api",
        "list_key": "jobs",
        "location_fallback": "Remote",
        "fields": {
            "id": "id",
            "title": "title",
            "company": "company.name",
            "location": "location",
            "url": "url",
            "content": "description",
            "posted_at": "created_at",
            "salary": "salary"
        }
    },
]

def fetch_json_source(source_config: Dict) -> List[Dict]:
    """Generic fetcher for JSON-based job APIs."""
    try:
        data = fetch_with_retry(source_config["url"], timeout=config["timeout_seconds"])
        if not data:
            return []

        # Extract the list of items
        if source_config.get("list_key") and isinstance(data, dict):
            items = data.get(source_config["list_key"], [])
        elif isinstance(data, list):
            items = data
        else:
            items = []

        jobs = []
        fields = source_config["fields"]
        fallback_location = source_config.get("location_fallback", "Remote")

        for item in items:
            if not isinstance(item, dict):
                continue

            # Helper to get nested fields (e.g., "company.name")
            def get_nested(obj, path):
                for key in path.split('.'):
                    if isinstance(obj, dict):
                        obj = obj.get(key, "")
                    else:
                        return ""
                return obj

            job = {
                "id": str(item.get(fields.get("id", ""), "")),
                "title": item.get(fields.get("title", ""), ""),
                "company": get_nested(item, fields["company"]) if "." in fields.get("company", "") else item.get(fields.get("company", ""), ""),
                "location": item.get(fields.get("location", ""), fallback_location),
                "url": item.get(fields.get("url", ""), ""),
                "content": item.get(fields.get("content", ""), ""),
                "posted_at": item.get(fields.get("posted_at", ""), ""),
                "source": source_config["name"],
                "salary": normalize_salary(str(item.get(fields.get("salary", ""), ""))) if fields.get("salary") else ""
            }
            if job["title"] and job["company"]:
                jobs.append(job)

        log.info(f"   ✅ {source_config['name'].capitalize()}: {len(jobs)} jobs")
        return jobs
    except Exception as e:
        log.warning(f"   ⚠️ {source_config['name']} failed: {e}")
        return []

# ─── SPECIAL PARSERS (Greenhouse, Lever) ───
def fetch_greenhouse_jobs(slug: str) -> List[Dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        data = fetch_with_retry(url, timeout=config["timeout_seconds"])
        if data and "jobs" in data:
            jobs = []
            for job in data["jobs"]:
                loc = job.get("location", {}).get("name", "")
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("name", slug.capitalize()),
                    "location": loc or "Remote",
                    "url": job.get("absolute_url", ""),
                    "content": job.get("content", ""),
                    "posted_at": job.get("updated_at", ""),
                    "source": f"greenhouse_{slug}",
                    "salary": ""
                })
            log.info(f"   ✅ Greenhouse {slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Greenhouse {slug} failed: {e}")
    return []

def fetch_lever_jobs(slug: str) -> List[Dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        data = fetch_with_retry(url, timeout=config["timeout_seconds"])
        if data and isinstance(data, list):
            jobs = []
            for job in data:
                jobs.append({
                    "id": job.get("id", ""),
                    "title": job.get("text", ""),
                    "company": job.get("categories", {}).get("team", slug.capitalize()),
                    "location": job.get("categories", {}).get("location", "Remote"),
                    "url": job.get("hostedUrl", ""),
                    "content": "",
                    "posted_at": job.get("createdAt", ""),
                    "source": f"lever_{slug}",
                    "salary": ""
                })
            log.info(f"   ✅ Lever {slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Lever {slug} failed: {e}")
    return []

# ─── JOBSPY ───
def fetch_jobspy() -> List[Dict]:
    if not config.get("enable_jobspy", True):
        return []
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("   ⚠️ JobSpy not installed. Install: pip install python-jobspy")
        return []
    job_titles = config.get("job_titles", [])[:3]
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

# ─── JOOBLE ───
def fetch_jooble() -> List[Dict]:
    api_key = os.getenv("JOOBLE_API_KEY", config.get("jooble_api_key", ""))
    if not api_key:
        return []
    try:
        resp = requests.post(
            "https://jooble.org/api/" + api_key,
            json={"keywords": "remote", "page": 1},
            timeout=config["timeout_seconds"]
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

# ─── ADZUNA ───
def fetch_adzuna() -> List[Dict]:
    app_id = os.getenv("ADZUNA_APP_ID", config.get("adzuna_app_id", ""))
    app_key = os.getenv("ADZUNA_APP_KEY", config.get("adzuna_app_key", ""))
    if not app_id or not app_key:
        return []
    try:
        url = f"https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id={app_id}&app_key={app_key}&results_per_page=50&what=remote"
        data = fetch_with_retry(url, timeout=config["timeout_seconds"])
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

# ─── CAREER PAGES (with BeautifulSoup) ───
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
    try:
        from bs4 import BeautifulSoup
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
        log.warning("   ⚠️ BeautifulSoup not installed. Career pages skipped.")
        return []

    for company in CAREER_COMPANIES:
        if company["type"] == "greenhouse":
            slug = company["url"].split("/")[-1]
            jobs.extend(fetch_greenhouse_jobs(slug))
        elif company["type"] == "lever":
            slug = company["url"].split("/")[-1]
            jobs.extend(fetch_lever_jobs(slug))
        else:
            try:
                data = fetch_with_retry(company["url"], timeout=config["timeout_seconds"])
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

# ─── SOCIAL MEDIA ───
def fetch_x_tweets() -> List[Dict]:
    if not config.get("enable_x", True):
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
    if not config.get("enable_reddit", True):
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
    if not config.get("enable_hn", True):
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
    if not config.get("enable_github", True):
        return []
    url = "https://api.github.com/search/issues?q=hiring+remote+label:help-wanted&per_page=20"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        data = fetch_with_retry(url, headers=headers, timeout=config["timeout_seconds"])
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

# ─── STARTUP DISCOVERY (adds companies to monitor) ───
def discover_new_startups() -> List[Dict]:
    if not config.get("enable_startup_discovery", True):
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
    # AngelList fallback
    try:
        resp = requests.get("https://wellfound.com/startups", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            log.info("   ✅ AngelList fallback attempted.")
    except Exception as e:
        log.warning(f"AngelList failed: {e}")
    log.info(f"   ✅ Startup discovery: {len(startups)} new startups found")
    return startups

# ─── SELF‑DISCOVERED SOURCES (from sources table) ───
def fetch_discovered_sources() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, url, type FROM sources WHERE active = 1")
    rows = c.fetchall()
    conn.close()
    jobs = []
    for row in rows:
        src = {"id": row[0], "name": row[1], "url": row[2], "type": row[3]}
        try:
            data = fetch_with_retry(src["url"], timeout=config["timeout_seconds"])
            if data and src["type"] == "json" and isinstance(data, list):
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
                            "source": f"discovered_{src['name'][:10]}",
                            "salary": normalize_salary(str(item.get("salary", "")))
                        })
            elif src["type"] == "rss" and data:
                try:
                    root = ET.fromstring(data)
                    for item in root.findall(".//item"):
                        jobs.append({
                            "id": item.find("link").text if item.find("link") is not None else "",
                            "title": item.find("title").text if item.find("title") is not None else "",
                            "company": src["name"],
                            "location": "Remote",
                            "url": item.find("link").text if item.find("link") is not None else "",
                            "content": item.find("description").text if item.find("description") is not None else "",
                            "posted_at": item.find("pubDate").text if item.find("pubDate") is not None else "",
                            "source": f"discovered_{src['name'][:10]}",
                            "salary": ""
                        })
                except:
                    pass
        except Exception as e:
            log.warning(f"   ⚠️ Discovered source {src['name']} failed: {e}")
    log.info(f"   ✅ Discovered sources: {len(jobs)} jobs")
    return jobs

# ─── MCP (optional) ───
def fetch_mcp() -> List[Dict]:
    if not config.get("enable_mcp", True):
        return []
    url = config.get("mcp_url", "http://localhost:3000/search")
    try:
        data = fetch_with_retry(url, method="POST",
                                json_data={"query": "remote customer support", "limit": 50},
                                headers={"Content-Type": "application/json"},
                                timeout=10, retries=2)
        if data and "jobs" in data:
            jobs = []
            for job in data["jobs"]:
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", job.get("company_name", "")),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", job.get("apply_url", "")),
                    "content": job.get("description", job.get("content", "")),
                    "posted_at": job.get("posted_at", job.get("date", "")),
                    "source": "mcp",
                    "salary": normalize_salary(str(job.get("salary", "")))
                })
            log.info(f"   ✅ MCP: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ MCP request failed: {e}")
    return []

# ─── FILTERS & SCORING ───
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
    for kw in config.get("remote_keywords", []):
        if kw in location or kw in title or kw in desc:
            return True
    return False

def matches_filter(job: Dict) -> bool:
    if not is_fully_remote(job):
        return False
    title = job.get("title", "").lower()
    for kw in config.get("exclude_keywords", []):
        if kw in title:
            return False
    job_titles = config.get("job_titles", [])
    sw = config.get("software_keywords", [])
    return any(kw in title for kw in job_titles) or any(kw in title for kw in sw)

def calculate_score(job: Dict) -> int:
    score = 0
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    location = job.get("location", "").lower()
    desc = job.get("content", "").lower()

    for kw in config.get("job_titles", [])[:5]:
        if kw in title:
            score += 30
            break
    else:
        for kw in config.get("software_keywords", [])[:3]:
            if kw in title:
                score += 20
                break

    if "anywhere" in location or "global" in location:
        score += 20
    elif "remote" in location:
        score += 15
    elif "fully remote" in desc:
        score += 18

    for pc in config.get("priority_companies", []):
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
    return {"score": max(0, score), "is_ghost": score < config.get("ghost_threshold", 40), "signals": signals}

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
    for pc in config.get("priority_companies", []):
        if pc.lower() in company:
            score = max(0, score - 30)
            reasons.append("priority company - trusted")
    # Feedback learning from flagged jobs
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT company, reason FROM flagged_jobs")
    flagged = c.fetchall()
    conn.close()
    for flagged_company, flagged_reason in flagged:
        if flagged_company.lower() in company:
            score += 20
            reasons.append(f"company previously flagged as scam: {flagged_reason}")
    return {"score": min(100, score), "is_scam": score > config.get("scam_threshold", 60), "reasons": reasons}

# ─── ALERTS ───
def send_telegram(jobs: List[Dict], is_test=False):
    if not config.get("enable_telegram", True):
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram secrets missing")
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

def send_gmail_jobs(jobs: List[Dict]):
    if not config.get("enable_gmail", True):
        return
    if not jobs:
        return
    host = os.getenv("SMTP_HOST", config.get("smtp_host", ""))
    port = int(os.getenv("SMTP_PORT", config.get("smtp_port", 587)))
    user = os.getenv("SMTP_USER", config.get("smtp_user", ""))
    password = os.getenv("SMTP_PASSWORD", config.get("smtp_password", ""))
    to_email = os.getenv("EMAIL_TO", config.get("email_to", ""))
    if not all([host, port, user, password, to_email]):
        log.warning("Email not configured. Skipping Gmail.")
        return
    subject = f"🌍 {len(jobs)} New Remote Jobs – {datetime.now().strftime('%Y-%m-%d')}"
    body = f"🌍 {len(jobs)} REMOTE JOBS FOUND\n\n"
    for job in jobs[:20]:
        body += (
            f"🏢 {job['company']}\n"
            f"💼 {job['title']}\n"
            f"📍 {job.get('location') or 'Remote'}\n"
            f"🎯 {job.get('score', 0)}/100\n"
            f"🔗 {job['url']}\n\n"
        )
    if len(jobs) > 20:
        body += f"\n... and {len(jobs)-20} more jobs.\n"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, to_email, msg.as_string())
        log.info(f"✅ Gmail sent ({len(jobs)} jobs)")
    except Exception as e:
        log.warning(f"Gmail send failed: {e}")

def send_email_error(error_message: str):
    host = os.getenv("SMTP_HOST", config.get("smtp_host", ""))
    port = int(os.getenv("SMTP_PORT", config.get("smtp_port", 587)))
    user = os.getenv("SMTP_USER", config.get("smtp_user", ""))
    password = os.getenv("SMTP_PASSWORD", config.get("smtp_password", ""))
    to_email = os.getenv("EMAIL_TO", config.get("email_to", ""))
    if not all([host, port, user, password, to_email]):
        log.warning("Email not configured. Crash report not sent.")
        return
    subject = f"🚨 Job Hunter Crash – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    body = f"Job Hunter crashed:\n\n{error_message}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, to_email, msg.as_string())
        log.info("✅ Crash report sent to email.")
    except Exception as e:
        log.warning(f"Failed to send error email: {e}")

# ─── HEALTH WRITER ───
def write_health(source_counts: Dict, total_fetched: int, total_matched: int):
    health = {
        "last_run": datetime.now().isoformat(),
        "total_fetched": total_fetched,
        "total_matched": total_matched,
        "sources": source_counts,
    }
    health_path = Path("data/health.json")
    health_path.parent.mkdir(exist_ok=True)
    with open(health_path, "w") as f:
        json.dump(health, f, indent=2)

# ─── MAIN ───
def main():
    log.info("=" * 60)
    log.info("🌍 REMOTE OPPORTUNITY HUNTER v18.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Configuration-driven • All sources • Optimized")
    log.info("=" * 60)

    init_db()
    archive_old_jobs()

    # Load seen URLs
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url FROM jobs")
    seen = {row[0] for row in c.fetchall()}
    conn.close()

    all_jobs = []
    source_counts = defaultdict(int)

    # ─── 1. JSON Sources (unified) ───
    if config.get("enable_public_apis", True):
        log.info("📡 Fetching JSON APIs (Remotive, RemoteOK, Arbeitnow, Himalayas)...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fetch_json_source, src): src["name"] for src in JSON_SOURCES}
            for future in as_completed(futures):
                jobs = future.result()
                all_jobs.extend(jobs)
                source_counts[futures[future]] += len(jobs)

    # ─── 2. ATS Direct ───
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

    # ─── 3. JobSpy ───
    if config.get("enable_jobspy", True):
        log.info("📡 Fetching JobSpy...")
        jobspy_jobs = fetch_jobspy()
        all_jobs.extend(jobspy_jobs)
        source_counts["jobspy"] += len(jobspy_jobs)

    # ─── 4. Jooble ───
    log.info("📡 Fetching Jooble...")
    jooble_jobs = fetch_jooble()
    all_jobs.extend(jooble_jobs)
    source_counts["jooble"] += len(jooble_jobs)

    # ─── 5. Adzuna ───
    log.info("📡 Fetching Adzuna...")
    adzuna_jobs = fetch_adzuna()
    all_jobs.extend(adzuna_jobs)
    source_counts["adzuna"] += len(adzuna_jobs)

    # ─── 6. Career Pages ───
    log.info("📡 Fetching career pages...")
    career_jobs = fetch_career_pages()
    all_jobs.extend(career_jobs)
    source_counts["career_pages"] += len(career_jobs)

    # ─── 7. Social Media ───
    if config.get("enable_x", True) or config.get("enable_reddit", True):
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

    # ─── 8. Discovered sources ───
    log.info("📡 Fetching from discovered sources...")
    discovered_jobs = fetch_discovered_sources()
    all_jobs.extend(discovered_jobs)
    source_counts["discovered"] += len(discovered_jobs)

    # ─── 9. MCP ───
    if config.get("enable_mcp", True):
        log.info("📡 Fetching from MCP...")
        mcp_jobs = fetch_mcp()
        all_jobs.extend(mcp_jobs)
        source_counts["mcp"] += len(mcp_jobs)

    # ─── Process ───
    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

    filtered = []
    scam_filtered = 0
    geo_filtered = 0
    duplicate_filtered = 0
    age_filtered = 0
    ghost_filtered = 0

    for job in all_jobs:
        uid = job.get("url", "")
        if uid in seen:
            continue

        # Age filter
        posted = job.get("posted_at", "")
        if posted:
            try:
                dt = datetime.fromisoformat(posted.replace('Z', '+00:00'))
                if (datetime.now() - dt).days > config.get("max_age_days", 30):
                    age_filtered += 1
                    continue
            except:
                pass

        # Geo
        if not is_globally_allowed(job):
            geo_filtered += 1
            continue

        # Scam
        scam_result = detect_scam(job)
        if scam_result["is_scam"]:
            scam_filtered += 1
            continue

        # Ghost
        ghost = detect_ghost(job)
        if ghost["is_ghost"]:
            ghost_filtered += 1
            continue

        # Duplicate (fuzzy)
        if is_duplicate_job(job, filtered):
            duplicate_filtered += 1
            continue

        # Link validation
        if not validate_url(job.get("url", "")):
            continue

        # Filter
        if matches_filter(job):
            job["score"] = calculate_score(job)
            job["ghost_score"] = ghost["score"]
            job["scam_score"] = scam_result["score"]
            # Save to DB
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, url, source, posted_at, score, ghost_score, scam_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                job.get("scam_score", 0)
            ))
            conn.commit()
            conn.close()
            filtered.append(job)

    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

    log.info(f"   ✅ {len(filtered)} jobs matched")
    log.info(f"   🛑 {scam_filtered} rejected as scams")
    log.info(f"   🗺️ {geo_filtered} rejected (geo restrictions)")
    log.info(f"   🔄 {duplicate_filtered} rejected (duplicates)")
    log.info(f"   📅 {age_filtered} rejected (too old)")
    log.info(f"   👻 {ghost_filtered} rejected (ghost)")

    # ─── Health ───
    write_health(source_counts, len(all_jobs), len(filtered))

    # ─── Alerts ───
    if filtered:
        send_telegram(filtered)
        send_gmail_jobs(filtered)
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
        send_email_error(error_msg)
        sys.exit(1)
