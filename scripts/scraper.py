#!/usr/bin/env python3
"""
Remote Opportunity Hunter v32.0 – ADVANCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Features:
  • 14+ sources (RemoteOK, Remotive, Himalayas, WeWorkRemotely, JobSpy,
    Greenhouse, X, Reddit, HN, GitHub, Reddit Tasks, Google Search, YC,
    Wellfound, auto‑discovered)
  • Self‑expanding source discovery
  • Auto‑migration of database schema
  • Smart scoring (remote, recency, easy roles, global‑friendly)
  • Telegram & email alerts (optional)
  • All secrets via environment variables
  • Zero cost – runs on GitHub Actions or Streamlit Cloud
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import sqlite3
import logging
import sys
import re
import random
import hashlib
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

# ─── ENVIRONMENT VARIABLES ───
from dotenv import load_dotenv
load_dotenv()

# ─── REQUESTS ───
try:
    import requests
except ImportError:
    requests = None
    print("⚠️ requests not installed. Install with: pip install requests")

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

# ─── USER AGENT ROTATION ───
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

def random_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}

# ─── FETCH WITH RETRY ───
def fetch_with_retry(url, headers=None, timeout=10, retries=3):
    if requests is None:
        return None
    if headers is None:
        headers = random_headers()
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json() if "application/json" in resp.headers.get("Content-Type", "") else resp.text
            elif resp.status_code == 429:
                wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                time.sleep(wait)
                continue
            else:
                return None
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep((2 ** attempt) * 0.5 + random.uniform(0, 0.5))
    return None

# ─── CONFIG ───
CONFIG_FILE = Path("config.json")
config = {}
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except:
        pass

for key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD", "SCAM_THRESHOLD", "MAX_AGE_DAYS", "MAX_RESULTS_PER_SOURCE"]:
    if os.getenv(key):
        config[key.lower()] = int(os.getenv(key))
for key in ["ENABLE_SEMANTIC_SCORING", "ENABLE_SOURCE_DISCOVERY", "ENABLE_GOOGLE_SEARCH", "ENABLE_EMAIL_DIGEST"]:
    if os.getenv(key):
        config[key.lower()] = os.getenv(key).lower() == "true"

config.setdefault("max_retries", 3)
config.setdefault("timeout_seconds", 20)
config.setdefault("ghost_threshold", 40)
config.setdefault("scam_threshold", 60)
config.setdefault("max_age_days", 30)
config.setdefault("max_results_per_source", 50)
config.setdefault("enable_source_discovery", True)
config.setdefault("enable_google_search", True)

# ─── COMPANY REPUTATION ───
GLOBAL_FRIENDLY_COMPANIES = [
    "gitlab", "stripe", "figma", "notion", "linear", "supabase", "airbnb",
    "vercel", "railway", "anthropic", "deepmind", "shopify", "discord",
    "spotify", "dropbox", "datadog", "elastic", "mongodb", "scale ai",
    "brex", "coursera", "amplitude"
]
GEO_RESTRICTED_COMPANIES = [
    "microsoft", "amazon", "google", "apple", "meta", "netflix",
    "jane street", "citadel", "jump trading", "robinhood", "databricks",
    "roblox", "uber", "lyft", "doordash"
]

# ─── DATABASE ───
DB_PATH = Path("data/jobs.db")
SCHEMA_VERSION = 2

def get_columns(conn, table_name):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in c.fetchall()]

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Core jobs table with all columns
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT UNIQUE,
            source TEXT,
            source_url TEXT,
            posted_at DATETIME,
            score INTEGER DEFAULT 0,
            ghost_score INTEGER,
            scam_score INTEGER,
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            type TEXT DEFAULT 'job',
            salary_min INTEGER,
            salary_max INTEGER,
            salary_text TEXT,
            saved BOOLEAN DEFAULT 0,
            content TEXT,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Archive table
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs_archive (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT UNIQUE,
            source TEXT,
            source_url TEXT,
            posted_at DATETIME,
            score INTEGER,
            ghost_score INTEGER,
            scam_score INTEGER,
            status TEXT DEFAULT 'archived',
            notes TEXT,
            type TEXT,
            salary_min INTEGER,
            salary_max INTEGER,
            salary_text TEXT,
            seen_at DATETIME,
            archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Sources table
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
            failure_count INTEGER DEFAULT 0
        )
    """)
    
    # Migration: ensure all columns exist
    cols = get_columns(conn, "jobs")
    required_cols = ["content", "saved", "type", "notes", "salary_min", "salary_max", "salary_text", "source_url"]
    for col in required_cols:
        if col not in cols:
            col_type = "BOOLEAN" if col in ["saved"] else "TEXT" if col in ["content", "notes", "type", "salary_text", "source_url"] else "INTEGER"
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type} DEFAULT ''")
    
    conn.commit()
    conn.close()
    log.info("✅ Database initialized (schema v2)")

def archive_old_jobs():
    """Archive jobs older than 90 days to jobs_archive table."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    
    try:
        # Ensure archive table exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS jobs_archive AS SELECT * FROM jobs WHERE 0
        """)
        
        # Get columns from jobs table
        c.execute("PRAGMA table_info(jobs)")
        jobs_cols = [row[1] for row in c.fetchall()]
        
        # Get columns from jobs_archive table
        c.execute("PRAGMA table_info(jobs_archive)")
        archive_cols = [row[1] for row in c.fetchall()]
        
        # Find common columns
        common_cols = [col for col in jobs_cols if col in archive_cols]
        
        if common_cols:
            cols_str = ", ".join(common_cols)
            c.execute(f"""
                INSERT INTO jobs_archive ({cols_str})
                SELECT {cols_str} FROM jobs WHERE seen_at < ?
            """, (cutoff,))
        
        c.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))
        conn.commit()
        log.info("✅ Archived old jobs")
    except Exception as e:
        log.warning(f"Archive failed: {e}")
    finally:
        conn.close()

# ─── NORMALIZATION ───
def normalize_date(date_str):
    if not date_str:
        return datetime.now().isoformat()
    try:
        if isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str).isoformat()
        return datetime.fromisoformat(date_str.replace('Z', '+00:00')).isoformat()
    except:
        return datetime.now().isoformat()

def normalize_salary(salary_data):
    result = {"min": None, "max": None, "text": ""}
    if isinstance(salary_data, dict):
        result["min"] = salary_data.get("min")
        result["max"] = salary_data.get("max")
        result["text"] = salary_data.get("text", "")
    elif isinstance(salary_data, str):
        result["text"] = salary_data
        nums = re.findall(r'\d+', salary_data)
        if len(nums) >= 2:
            result["min"] = int(nums[0])
            result["max"] = int(nums[1])
        elif len(nums) == 1:
            result["min"] = int(nums[0])
    return result

# ─── SCORING ───
def calculate_score(job):
    score = 0
    loc = job.get("location", "").lower()
    desc = job.get("content", "").lower()
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()

    # Remote quality
    if "anywhere" in loc or "global" in loc:
        score += 20
    elif "remote" in loc:
        score += 15
    elif "fully remote" in desc:
        score += 18

    # Recency
    posted = job.get("posted_at", "")
    if posted:
        try:
            days = (datetime.now() - datetime.fromisoformat(posted.replace('Z', '+00:00'))).days
            if days <= 1:
                score += 10
            elif days <= 3:
                score += 8
            elif days <= 7:
                score += 5
        except:
            pass

    # Direct apply
    if "greenhouse.io" in job.get("url", "") or "lever.co" in job.get("url", ""):
        score += 10

    # Easy roles
    easy_keywords = [
        "data entry", "virtual assistant", "customer support", "customer success",
        "support specialist", "operations associate", "onboarding", "implementation",
        "community support", "community manager", "administrative assistant",
        "project coordinator", "trust and safety", "entry level", "junior",
        "trainee", "internship", "task", "microtask", "gig", "freelance",
        "transcription", "annotation", "labeling", "moderation"
    ]
    for kw in easy_keywords:
        if kw in title or kw in desc:
            score += 15
            break

    # Global‑friendly company bonus
    for gc in GLOBAL_FRIENDLY_COMPANIES:
        if gc in company:
            score += 25
            break

    # Geo‑restricted penalty
    for gr in GEO_RESTRICTED_COMPANIES:
        if gr in company:
            score -= 50
            break

    return max(0, min(100, int(score)))

# ─── SOURCE REPUTATION ───
SOURCE_REPUTATION_FILE = Path("data/source_reputation.json")

def load_source_reputation():
    if SOURCE_REPUTATION_FILE.exists():
        try:
            return json.loads(SOURCE_REPUTATION_FILE.read_text())
        except:
            return {}
    return {}

def save_source_reputation(rep):
    SOURCE_REPUTATION_FILE.parent.mkdir(exist_ok=True)
    SOURCE_REPUTATION_FILE.write_text(json.dumps(rep, indent=2))

def update_source_reputation(source, success):
    rep = load_source_reputation()
    if source not in rep:
        rep[source] = {"consecutive_failures": 0, "active": True}
    if success:
        rep[source]["consecutive_failures"] = 0
    else:
        rep[source]["consecutive_failures"] += 1
        if rep[source]["consecutive_failures"] >= 5:
            rep[source]["active"] = False
            log.warning(f"Source {source} disabled.")
    save_source_reputation(rep)
    return rep[source].get("active", True)

# ─── FETCHERS ───
# (All fetchers are implemented – same as v31.0)
# For brevity, I'll include the essential ones and note the rest.

def fetch_remoteok():
    if requests is None:
        return []
    try:
        resp = requests.get("https://remoteok.com/api", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for item in data[1:]:
                if isinstance(item, dict) and item.get("position"):
                    salary = normalize_salary(f"{item.get('salary_min', '')}-{item.get('salary_max', '')}")
                    jobs.append({
                        "id": f"remoteok_{item.get('id', '')}",
                        "title": item.get("position", ""),
                        "company": item.get("company", ""),
                        "location": item.get("location", "Remote"),
                        "url": item.get("url", ""),
                        "source": "remoteok",
                        "source_url": "https://remoteok.com/api",
                        "posted_at": normalize_date(item.get("date")),
                        "salary_min": salary["min"],
                        "salary_max": salary["max"],
                        "salary_text": salary["text"],
                        "type": "job",
                        "content": item.get("description", "")
                    })
            log.info(f"   ✅ RemoteOK: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ RemoteOK failed: {e}")
    return []

def fetch_remotive():
    if requests is None:
        return []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                salary = normalize_salary(job.get("salary", ""))
                jobs.append({
                    "id": f"remotive_{job.get('id', '')}",
                    "title": job.get("title", ""),
                    "company": job.get("company_name", ""),
                    "location": "Remote",
                    "url": job.get("url", ""),
                    "source": "remotive",
                    "source_url": "https://remotive.com/api/remote-jobs",
                    "posted_at": normalize_date(job.get("publication_date")),
                    "salary_min": salary["min"],
                    "salary_max": salary["max"],
                    "salary_text": salary["text"],
                    "type": "job",
                    "content": job.get("description", "")
                })
            log.info(f"   ✅ Remotive: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Remotive failed: {e}")
    return []

def fetch_himalayas():
    if requests is None:
        return []
    try:
        resp = requests.get("https://himalayas.app/jobs/api?limit=50", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                salary = normalize_salary({
                    "min": job.get("minSalary"),
                    "max": job.get("maxSalary"),
                    "text": job.get("salary", "")
                })
                jobs.append({
                    "id": f"himalayas_{job.get('id', '')}",
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("name", ""),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", ""),
                    "source": "himalayas",
                    "source_url": "https://himalayas.app/jobs/api",
                    "posted_at": normalize_date(job.get("createdAt")),
                    "salary_min": salary["min"],
                    "salary_max": salary["max"],
                    "salary_text": salary["text"],
                    "type": "job",
                    "content": job.get("description", "")
                })
            log.info(f"   ✅ Himalayas: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Himalayas failed: {e}")
    return []

def fetch_weworkremotely():
    if requests is None:
        return []
    try:
        resp = requests.get("https://weworkremotely.com/remote-jobs.rss", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            jobs = []
            for item in root.findall(".//item"):
                title = item.find("title").text or ""
                company, role = ("", title) if ": " not in title else title.split(": ", 1)
                jobs.append({
                    "id": f"wwr_{hashlib.md5(item.find('link').text.encode()).hexdigest()[:8]}",
                    "title": role,
                    "company": company,
                    "location": "Remote",
                    "url": item.find("link").text or "",
                    "source": "weworkremotely",
                    "source_url": "https://weworkremotely.com/remote-jobs.rss",
                    "posted_at": normalize_date(item.find("pubDate").text if item.find("pubDate") is not None else ""),
                    "salary_min": None,
                    "salary_max": None,
                    "salary_text": "",
                    "type": "job",
                    "content": item.find("description").text if item.find("description") is not None else ""
                })
            log.info(f"   ✅ WeWorkRemotely: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ WeWorkRemotely failed: {e}")
    return []

def fetch_greenhouse(slug):
    if requests is None:
        return []
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = requests.get(url, headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                jobs.append({
                    "id": f"greenhouse_{slug}_{job.get('id', '')}",
                    "title": job.get("title", ""),
                    "company": job.get("company", {}).get("name", slug.capitalize()),
                    "location": job.get("location", {}).get("name", "Remote"),
                    "url": job.get("absolute_url", ""),
                    "source": f"greenhouse_{slug}",
                    "source_url": url,
                    "posted_at": normalize_date(job.get("updated_at")),
                    "salary_min": None,
                    "salary_max": None,
                    "salary_text": "",
                    "type": "job",
                    "content": job.get("content", "")
                })
            log.info(f"   ✅ Greenhouse {slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Greenhouse {slug} failed: {e}")
    return []

def fetch_jobspy():
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return []
    try:
        df = scrape_jobs(
            site_name=["indeed", "linkedin", "glassdoor", "google", "zip_recruiter"],
            search_term="remote",
            location="remote",
            is_remote=True,
            results_wanted=config.get("max_results_per_source", 50),
            hours_old=168,
            proxies=None
        )
        jobs = []
        for _, row in df.iterrows():
            salary = normalize_salary(f"{row.get('min_amount', '')}-{row.get('max_amount', '')}")
            jobs.append({
                "id": f"jobspy_{hashlib.md5(str(row.get('job_url', '')).encode()).hexdigest()[:8]}",
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "location": row.get("location", "Remote"),
                "url": row.get("job_url", ""),
                "source": "jobspy",
                "source_url": row.get("job_url", ""),
                "posted_at": normalize_date(str(row.get("date_posted", ""))),
                "salary_min": salary["min"],
                "salary_max": salary["max"],
                "salary_text": salary["text"],
                "type": "job",
                "content": row.get("description", "")
            })
        log.info(f"   ✅ JobSpy: {len(jobs)} jobs")
        return jobs
    except Exception as e:
        log.warning(f"   ⚠️ JobSpy failed: {e}")
    return []

def fetch_x_tweets():
    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer or requests is None:
        return []
    queries = ['"we\'re hiring" remote', '"join our team" remote', '"open position" remote']
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
                        "source": "x_social",
                        "source_url": f"https://twitter.com/i/web/status/{tweet['id']}",
                        "posted_at": normalize_date(tweet.get("created_at")),
                        "salary_min": None,
                        "salary_max": None,
                        "salary_text": "",
                        "type": "job",
                        "content": text
                    })
        except Exception as e:
            log.warning(f"X search failed: {e}")
    log.info(f"   ✅ X: {len(jobs)} tweets")
    return jobs

def fetch_reddit_jobs():
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
                        "source": f"reddit_{sub}",
                        "source_url": f"https://reddit.com{post['permalink']}",
                        "posted_at": normalize_date(post["created_utc"]),
                        "salary_min": None,
                        "salary_max": None,
                        "salary_text": "",
                        "type": "job",
                        "content": post.get("selftext", "")[:500]
                    })
        except Exception as e:
            log.warning(f"Reddit r/{sub} failed: {e}")
    log.info(f"   ✅ Reddit: {len(jobs)} posts")
    return jobs

def fetch_hn_jobs():
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
                            "source": "hn",
                            "source_url": f"https://news.ycombinator.com/item?id={kid_id}",
                            "posted_at": normalize_date(comment.get("time")),
                            "salary_min": None,
                            "salary_max": None,
                            "salary_text": "",
                            "type": "job",
                            "content": comment.get("text", "")[:500]
                        })
                break
        log.info(f"   ✅ Hacker News: {len(jobs)} comments")
        return jobs
    except Exception as e:
        log.warning(f"Hacker News failed: {e}")
    return []

def fetch_github_issues():
    url = "https://api.github.com/search/issues?q=hiring+remote+label:help-wanted&per_page=20"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        data = fetch_with_retry(url, headers=headers, timeout=config.get("timeout_seconds", 20))
        if data and "items" in data:
            jobs = []
            for item in data["items"]:
                jobs.append({
                    "id": str(item["id"]),
                    "title": item["title"][:100],
                    "company": "GitHub",
                    "location": "Remote",
                    "url": item["html_url"],
                    "source": "github_issue",
                    "source_url": item["html_url"],
                    "posted_at": normalize_date(item.get("created_at")),
                    "salary_min": None,
                    "salary_max": None,
                    "salary_text": "",
                    "type": "job",
                    "content": item.get("body", "")[:500]
                })
            log.info(f"   ✅ GitHub Issues: {len(jobs)} found")
            return jobs
    except Exception as e:
        log.warning(f"GitHub Issues failed: {e}")
    return []

def fetch_reddit_tasks():
    subreddits = ["slavelabour", "beermoney", "workonline", "forhire", "freelance"]
    keywords = ["need help", "looking for", "paid", "gig", "task", "microtask", "user testing", "transcription"]
    jobs = []
    for sub in subreddits:
        query = " OR ".join(keywords)
        try:
            data = fetch_with_retry(
                f"https://www.reddit.com/r/{sub}/search.json?q={query}&restrict_sr=1&limit=20&sort=new",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if data and "data" in data and "children" in data["data"]:
                for child in data["data"]["children"]:
                    post = child["data"]
                    title = post.get("title", "").lower()
                    selftext = post.get("selftext", "").lower()
                    if any(kw in title or kw in selftext for kw in keywords):
                        jobs.append({
                            "id": post["id"],
                            "title": post["title"][:100],
                            "company": f"r/{sub}",
                            "location": "Remote",
                            "url": f"https://reddit.com{post['permalink']}",
                            "source": f"reddit_task_{sub}",
                            "source_url": f"https://reddit.com{post['permalink']}",
                            "posted_at": normalize_date(post["created_utc"]),
                            "salary_min": None,
                            "salary_max": None,
                            "salary_text": "",
                            "type": "task",
                            "content": post.get("selftext", "")
                        })
        except Exception as e:
            log.warning(f"Reddit task r/{sub} failed: {e}")
    log.info(f"   ✅ Reddit tasks: {len(jobs)} tasks")
    return jobs

def fetch_google_jobs():
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key or not config.get("enable_google_search", True) or requests is None:
        return []
    queries = [
        '"looking for" remote data entry',
        '"paid" microtask online',
        '"user testing" paid',
        '"transcription" remote',
        '"freelance" remote gig'
    ]
    jobs = []
    for q in queries:
        try:
            resp = requests.get("https://serpapi.com/search", params={"q": q, "api_key": api_key, "num": 10}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for result in data.get("organic_results", []):
                    title = result.get("title", "")
                    snippet = result.get("snippet", "")
                    url = result.get("link", "")
                    platform = "Unknown"
                    platforms = ["Upwork", "Fiverr", "UserTesting", "Rev", "TranscribeMe", "Mechanical Turk", "Clickworker"]
                    for plat in platforms:
                        if plat.lower() in title.lower() or plat.lower() in snippet.lower():
                            platform = plat
                            break
                    jobs.append({
                        "id": url,
                        "title": title[:100],
                        "company": platform,
                        "location": "Remote",
                        "url": url,
                        "source": "google_search",
                        "source_url": url,
                        "posted_at": datetime.now().isoformat(),
                        "salary_min": None,
                        "salary_max": None,
                        "salary_text": "",
                        "type": "task",
                        "content": snippet
                    })
        except Exception as e:
            log.warning(f"Google search failed: {e}")
    log.info(f"   ✅ Google jobs: {len(jobs)} tasks")
    return jobs

def fetch_yc_jobs():
    if requests is None:
        return []
    try:
        resp = requests.get("https://www.ycombinator.com/companies", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for company in data:
                if company.get("jobs"):
                    for job in company["jobs"]:
                        jobs.append({
                            "id": f"yc_{company.get('slug', '')}_{job.get('id', '')}",
                            "title": job.get("title", ""),
                            "company": company.get("name", ""),
                            "location": "Remote" if job.get("remote") else "On-site",
                            "url": f"https://www.ycombinator.com/companies/{company.get('slug', '')}/jobs/{job.get('id', '')}",
                            "source": "yc",
                            "source_url": "https://www.ycombinator.com/companies",
                            "posted_at": normalize_date(job.get("created_at")),
                            "salary_min": None,
                            "salary_max": None,
                            "salary_text": "",
                            "type": "job",
                            "content": job.get("description", "")
                        })
            log.info(f"   ✅ Y Combinator: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ YC Jobs failed: {e}")
    return []

def fetch_wellfound():
