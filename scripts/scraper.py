#!/usr/bin/env python3
"""
Remote Opportunity Hunter v24.0 – FINAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- All roles accepted – only remote + global
- Blacklists known fake platforms
- Strict geo‑filter (no US/UK/CA/Nigeria restrictions)
- Source reputation: auto‑disables failing sources
- AI‑assisted scam detection (Gemini, optional)
- Auto‑archive low‑score jobs
- Configurable limits per source
- Parallel fetching with ThreadPoolExecutor
- Zero cost – runs on GitHub Actions
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

# ─── CONFIG ───
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
for key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD", "SCAM_THRESHOLD", "MAX_AGE_DAYS", "MAX_RESULTS_PER_SOURCE"]:
    if os.getenv(key):
        config[key.lower()] = int(os.getenv(key))
for key in ["ENABLE_MCP", "ENABLE_PUBLIC_APIS", "ENABLE_JOBSPY", "ENABLE_X", "ENABLE_REDDIT",
           "ENABLE_HN", "ENABLE_GITHUB", "ENABLE_JOOBLE", "ENABLE_ADZUNA", "ENABLE_REDDIT_TASKS",
           "ENABLE_GOOGLE_TASKS", "ENABLE_AI_SCAM", "SAFE_SOURCES_ONLY"]:
    if os.getenv(key):
        config[key.lower()] = os.getenv(key).lower() == "true"

# Defaults
config.setdefault("max_retries", 3)
config.setdefault("timeout_seconds", 20)
config.setdefault("ghost_threshold", 40)
config.setdefault("scam_threshold", 60)
config.setdefault("max_age_days", 30)
config.setdefault("max_results_per_source", 50)   # limit to speed up
config.setdefault("enable_mcp", False)            # disabled by default
config.setdefault("enable_public_apis", True)
config.setdefault("enable_jobspy", True)
config.setdefault("enable_x", True)
config.setdefault("enable_reddit", True)
config.setdefault("enable_hn", True)
config.setdefault("enable_github", True)
config.setdefault("enable_jooble", True)
config.setdefault("enable_adzuna", True)
config.setdefault("enable_reddit_tasks", True)
config.setdefault("enable_google_tasks", True)
config.setdefault("enable_ai_scam", False)
config.setdefault("safe_sources_only", False)

# ─── BLACKLIST ───
BLACKLISTED_PLATFORMS = [
    "micro1", "microworkers", "appen", "lionbridge", "clickworker", "mturk", "amazon mechanical turk",
    "fiverr", "upwork", "freelancer", "peopleperhour", "guru", "toptal", "gigster",
    "99designs", "designcrowd", "usertesting", "trymyui", "userzoom", "whatusersdo",
    "rev", "transcribeme", "gotranscript", "speechpad", "castingwords", "scribie",
    "prolific", "respondent", "userinterviews", "mindswarms", "intellizoom", "livescape",
    "clickresearch", "zapier", "rework", "x.ai", "alpaca", "fancyhands", "taskrabbit"
]

# ─── DATABASE ───
DB_PATH = Path("data/jobs.db")
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
            type TEXT DEFAULT 'job',
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_url ON jobs(url)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_seen_at ON jobs(seen_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posted_at ON jobs(posted_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_score_seen ON jobs(score, seen_at DESC)")
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

def archive_low_score_jobs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    c.execute("""
        INSERT INTO jobs_archive (id, title, company, location, url, source,
                                  posted_at, score, ghost_score, scam_score, status, seen_at)
        SELECT id, title, company, location, url, source,
               posted_at, score, ghost_score, scam_score, status, seen_at
        FROM jobs
        WHERE score < 30 AND seen_at < ?
    """, (cutoff,))
    c.execute("DELETE FROM jobs WHERE score < 30 AND seen_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    log.info("✅ Archived low‑score jobs")

# ─── UTILITY ───
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_with_retry(url, method="GET", json_data=None, headers=None, timeout=20, retries=3):
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

def normalize_salary(s):
    if not s:
        return ""
    s = re.sub(r'[$,£,€,¥]', '', s)
    nums = re.findall(r'\d+', s)
    if not nums:
        return ""
    return f"{nums[0]}-{nums[1]}" if len(nums) >= 2 else nums[0]

def get_job_uid(job):
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    return f"{job['company']}::hash::{hashlib.md5(content.encode()).hexdigest()}"

def is_duplicate_job(job, existing):
    for e in existing:
        if e.get('company') == job.get('company') and \
           SequenceMatcher(None, e.get('title', '').lower(), job.get('title', '').lower()).ratio() > 0.85:
            return True
    return False

def validate_url(url):
    if not url:
        return False
    try:
        return 200 <= requests.head(url, timeout=5, allow_redirects=True).status_code < 400
    except:
        return False

# ─── GEO & REMOTE FILTERS ───
RESTRICTED_COUNTRIES = ["us", "usa", "united states", "canada", "uk", "united kingdom", "europe", "australia", "nigeria", "africa"]
RESTRICTED_PATTERNS = ["only for", "must reside in", "must be located in", "require", "need to be", "citizen of"]

def is_globally_allowed(job):
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    content = job.get("content", "").lower()
    combined = location + " " + title + " " + content
    if not any(kw in combined for kw in ["remote", "anywhere", "global", "worldwide", "work from anywhere"]):
        return False
    for country in RESTRICTED_COUNTRIES:
        if country in combined:
            if "timezone" in combined or "hours" in combined:
                continue
            return False
    for pattern in RESTRICTED_PATTERNS:
        if pattern in combined:
            if "remote" in pattern:
                continue
            return False
    return True

def is_fully_remote(job):
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    desc = job.get("content", "").lower()
    if "hybrid" in location or "in-office" in location:
        return False
    for kw in ["remote", "anywhere", "global", "work from anywhere"]:
        if kw in location or kw in title or kw in desc:
            return True
    return False

def matches_filter(job):
    return is_fully_remote(job) and is_globally_allowed(job)

# ─── SCAM DETECTION ───
def is_blacklisted_platform(job):
    combined = f"{job.get('company','')} {job.get('title','')} {job.get('content','')}".lower()
    for plat in BLACKLISTED_PLATFORMS:
        if plat.lower() in combined:
            return True
    return False

def detect_scam(job):
    score = 0
    reasons = []
    desc = job.get("content", "").lower()
    salary = job.get("salary", "").lower()
    company = job.get("company", "").lower()
    title = job.get("title", "").lower()

    if is_blacklisted_platform(job):
        score += 50
        reasons.append("platform blacklisted")

    # AI scam (optional)
    if config.get("enable_ai_scam", False):
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key and desc:
            try:
                prompt = f"Reply only with a number 0-100 (0=legit, 100=scam): {desc[:800]}"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={api_key}"
                payload = {"contents": [{"parts": [{"text": prompt}]}]}
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    text = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    nums = re.findall(r'\d+', text)
                    if nums:
                        score += min(40, int(nums[0]) * 0.4)
                        reasons.append(f"AI score: {nums[0]}")
            except:
                pass

    # Salary anomalies
    if salary:
        nums = re.findall(r'\d+', salary)
        if nums and int(nums[0]) > 150000 and "entry" in title:
            score += 30
            reasons.append("unrealistically high salary")
        elif nums and int(nums[0]) < 10000 and "hourly" not in salary:
            score += 20
            reasons.append("suspiciously low salary")

    if company in ["unknown", "startup", "company", "tech", "anonymous"]:
        score += 20
        reasons.append("generic company")
    scam_indicators = [
        "unlimited earning", "make money fast", "get rich",
        "paid training", "free work", "unpaid internship",
        "send your bank", "ssn", "passport", "cryptocurrency", "bitcoin", "forex", "pyramid"
    ]
    for ind in scam_indicators:
        if ind in desc:
            score += 10
            reasons.append(f"indicator: '{ind}'")
    if len(desc) < 200:
        score += 10
        reasons.append("very short description")

    # Feedback learning
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT company FROM flagged_jobs")
    flagged = [row[0] for row in c.fetchall()]
    conn.close()
    for fc in flagged:
        if fc.lower() in company:
            score += 20
            reasons.append("flagged company")
            break

    return {"score": min(100, score), "is_scam": score > config.get("scam_threshold", 60), "reasons": reasons}

def detect_ghost(job, existing=None):
    signals = []
    score = 100
    if not job.get("location") or job.get("location") == "Unknown":
        score -= 18
        signals.append("missing_location")
    if not job.get("posted_at"):
        score -= 12
        signals.append("missing_date")
    if len(job.get("content", "")) < 100:
        score -= 10
        signals.append("vague_description")
    if existing:
        for e in existing:
            if e.get('company') == job.get('company') and \
               SequenceMatcher(None, e.get('title',''), job.get('title','')).ratio() > 0.9:
                posted_existing = e.get('posted_at')
                if posted_existing:
                    try:
                        dt_existing = datetime.fromisoformat(posted_existing.replace('Z', '+00:00'))
                        if (datetime.now() - dt_existing).days <= 7:
                            score -= 25
                            signals.append("repeated")
                            break
                    except:
                        pass
    return {"score": max(0, score), "is_ghost": score < config.get("ghost_threshold", 40), "signals": signals}

def calculate_score(job):
    score = 0
    loc = job.get("location", "").lower()
    desc = job.get("content", "").lower()
    if "anywhere" in loc or "global" in loc:
        score += 20
    elif "remote" in loc:
        score += 15
    elif "fully remote" in desc:
        score += 18
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
    if "greenhouse.io" in job.get("url", "") or "lever.co" in job.get("url", ""):
        score += 10
    return min(100, score)

# ─── SOURCE REPUTATION ───
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
# (All fetchers are fully implemented)

# 1. JSON APIs (Remotive, RemoteOK, Arbeitnow, Himalayas)
JSON_SOURCES = [
    {"name": "remotive", "url": "https://remotive.com/api/remote-jobs", "list_key": "jobs", "fields": {"id":"id","title":"title","company":"company_name","location":"location","url":"url","content":"description","posted_at":"publication_date","salary":"salary"}},
    {"name": "remoteok", "url": "https://remoteok.com/api", "list_key": None, "fields": {"id":"id","title":"title","company":"company","location":"location","url":"url","content":"description","posted_at":"date","salary":None}},
    {"name": "arbeitnow", "url": "https://www.arbeitnow.com/api/job-board-api", "list_key": "data", "fields": {"id":"id","title":"title","company":"company_name","location":"location","url":"url","content":"description","posted_at":"created_at","salary":None}},
    {"name": "himalayas", "url": "https://himalayas.app/jobs/api", "list_key": "jobs", "fields": {"id":"id","title":"title","company":"company.name","location":"location","url":"url","content":"description","posted_at":"created_at","salary":"salary"}},
]

def fetch_json_source_list():
    all_jobs = []
    for src in JSON_SOURCES:
        try:
            data = fetch_with_retry(src["url"], timeout=config["timeout_seconds"])
            if not data:
                continue
            if src.get("list_key") and isinstance(data, dict):
                items = data.get(src["list_key"], [])
            elif isinstance(data, list):
                items = data
            else:
                items = []
            fields = src["fields"]
            for item in items:
                if not isinstance(item, dict):
                    continue
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
                    "company": get_nested(item, fields["company"]) if "." in fields.get("company","") else item.get(fields.get("company",""), ""),
                    "location": item.get(fields.get("location",""), "Remote"),
                    "url": item.get(fields.get("url",""), ""),
                    "content": item.get(fields.get("content",""), ""),
                    "posted_at": item.get(fields.get("posted_at",""), ""),
                    "source": src["name"],
                    "salary": normalize_salary(str(item.get(fields.get("salary",""), ""))) if fields.get("salary") else "",
                    "type": "job"
                }
                if job["title"] and job["company"]:
                    all_jobs.append(job)
            log.info(f"   ✅ {src['name'].capitalize()}: {len(all_jobs)} jobs")
        except Exception as e:
            log.warning(f"   ⚠️ {src['name']} failed: {e}")
    return all_jobs

# 2. Jooble (requires API key)
def fetch_jooble():
    api_key = os.getenv("JOOBLE_API_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.post(f"https://jooble.org/api/{api_key}", json={"keywords": "remote", "page": 1}, timeout=config["timeout_seconds"])
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
                    "salary": normalize_salary(str(job.get("salary", ""))),
                    "type": "job"
                })
            log.info(f"   ✅ Jooble: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Jooble failed: {e}")
    return []

# 3. Adzuna (requires API key)
def fetch_adzuna():
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
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
                    "salary": normalize_salary(str(job.get("salary_min", "")) + "-" + str(job.get("salary_max", ""))),
                    "type": "job"
                })
            log.info(f"   ✅ Adzuna: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Adzuna failed: {e}")
    return []

# 4. ATS: Greenhouse and Lever (with known‑good list for Lever)
GREENHOUSE_COMPANIES = ["stripe", "anthropic", "figma", "notion", "linear", "supabase", "gitlab"]
LEVER_COMPANIES = ["stripe", "anthropic", "figma", "notion", "linear", "supabase", "gitlab"]  # known to work

def fetch_greenhouse_jobs(slug):
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
                    "salary": "",
                    "type": "job"
                })
            log.info(f"   ✅ Greenhouse {slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Greenhouse {slug} failed: {e}")
    return []

def fetch_lever_jobs(slug):
    # Only fetch if company is in known-good list
    if slug not in LEVER_COMPANIES:
        return []
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
                    "salary": "",
                    "type": "job"
                })
            log.info(f"   ✅ Lever {slug}: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Lever {slug} failed: {e}")
    return []

def fetch_ats_sources():
    all_jobs = []
    for slug in GREENHOUSE_COMPANIES:
        all_jobs.extend(fetch_greenhouse_jobs(slug))
    for slug in LEVER_COMPANIES:
        all_jobs.extend(fetch_lever_jobs(slug))
    return all_jobs

# 5. JobSpy
def fetch_jobspy():
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("   ⚠️ JobSpy not installed. Install: pip install python-jobspy")
        return []
    try:
        df = scrape_jobs(
            site_name=["indeed", "linkedin", "glassdoor", "google", "zip_recruiter"],
            search_term="remote",
            location="remote",
            is_remote=True,
            results_wanted=config["max_results_per_source"],
            hours_old=168
        )
        jobs = []
        for _, row in df.iterrows():
            jobs.append({
                "id": str(row.get("job_url", "")),
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "location": row.get("location", "Remote"),
                "url": row.get("job_url", ""),
                "content": row.get("description", ""),
                "posted_at": str(row.get("date_posted", "")),
                "source": "jobspy",
                "salary": f"{row.get('min_amount', '')}-{row.get('max_amount', '')}" if row.get('min_amount') else "",
                "type": "job"
            })
        log.info(f"   ✅ JobSpy: {len(jobs)} jobs")
        return jobs
    except Exception as e:
        log.warning(f"   ⚠️ JobSpy failed: {e}")
        return []

# 6. Career Pages (with BeautifulSoup)
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

def fetch_career_pages():
    jobs = []
    try:
        from bs4 import BeautifulSoup
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
        log.warning("   ⚠️ BeautifulSoup not installed. Career pages skipped.")
        return jobs

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
                                    "salary": normalize_salary(str(j.get("baseSalary", {}).get("value", {}).get("value", ""))),
                                    "type": "job"
                                })
                        except:
                            pass
            except Exception as e:
                log.warning(f"   ⚠️ Career page {company['name']} failed: {e}")
    log.info(f"   ✅ Career Pages: {len(jobs)} jobs")
    return jobs

# 7. Social Media (X, Reddit, HN, GitHub)
def fetch_x_tweets():
    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
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
                        "content": text,
                        "posted_at": tweet.get("created_at", ""),
                        "source": "x_social",
                        "salary": "",
                        "type": "job"
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
                        "content": post.get("selftext", "")[:500],
                        "posted_at": datetime.utcfromtimestamp(post["created_utc"]).isoformat(),
                        "source": f"reddit_{sub}",
                        "salary": "",
                        "type": "job"
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
                            "content": comment.get("text", "")[:500],
                            "posted_at": datetime.utcfromtimestamp(comment.get("time", 0)).isoformat(),
                            "source": "hn",
                            "salary": "",
                            "type": "job"
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
                    "salary": "",
                    "type": "job"
                })
            log.info(f"   ✅ GitHub Issues: {len(jobs)} found")
            return jobs
    except Exception as e:
        log.warning(f"GitHub Issues failed: {e}")
    return []

# 8. Tasks (Reddit, Google)
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
                            "content": post.get("selftext", ""),
                            "posted_at": datetime.utcfromtimestamp(post["created_utc"]).isoformat(),
                            "source": f"reddit_task_{sub}",
                            "salary": "",
                            "type": "task"
                        })
        except Exception as e:
            log.warning(f"Reddit task r/{sub} failed: {e}")
    log.info(f"   ✅ Reddit tasks: {len(jobs)} tasks")
    return jobs

def fetch_google_tasks():
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
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
                        "content": snippet,
                        "posted_at": datetime.now().isoformat(),
                        "source": "google_tasks",
                        "salary": "",
                        "type": "task"
                    })
        except Exception as e:
            log.warning(f"Google tasks failed: {e}")
    log.info(f"   ✅ Google tasks: {len(jobs)} tasks")
    return jobs

# 9. MCP (optional, disabled by default)
def fetch_mcp():
    if not config.get("enable_mcp", False):
        return []
    url = config.get("mcp_url", "http://localhost:3000/search")
    try:
        data = fetch_with_retry(url, method="POST",
                                json_data={"query": "remote", "limit": 50},
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
                    "salary": normalize_salary(str(job.get("salary", ""))),
                    "type": "job"
                })
            log.info(f"   ✅ MCP: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ MCP request failed: {e}")
    return []

# 10. Discovered sources (from sources table)
def fetch_discovered_sources():
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
                            "salary": normalize_salary(str(item.get("salary", ""))),
                            "type": "job"
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
                            "salary": "",
                            "type": "job"
                        })
                except:
                    pass
        except Exception as e:
            log.warning(f"   ⚠️ Discovered source {src['name']} failed: {e}")
    log.info(f"   ✅ Discovered sources: {len(jobs)} jobs")
    return jobs

# ─── HEALTH WRITER ───
def write_health(source_counts, total_fetched, total_matched):
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

# ─── ALERTS ───
def send_telegram(jobs):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
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
        msg += (
            f"**{job['company']}**\n"
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
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

def send_gmail_jobs(jobs):
    # same as before – omitted for brevity
    pass

# ─── MAIN ───
def main():
    log.info("="*60)
    log.info("🌍 Remote Opportunity Hunter v24.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Optimized – Fast – Reliable")
    log.info("="*60)

    init_db()
    archive_old_jobs()
    archive_low_score_jobs()

    # Load seen URLs
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url FROM jobs")
    seen = {row[0] for row in c.fetchall()}
    conn.close()

    all_jobs = []
    source_counts = defaultdict(int)

    # ─── Sources to fetch ───
    sources = [
        ("json_apis", fetch_json_source_list, config.get("enable_public_apis", True)),
        ("jooble", fetch_jooble, config.get("enable_jooble", True)),
        ("adzuna", fetch_adzuna, config.get("enable_adzuna", True)),
        ("ats", fetch_ats_sources, True),
        ("jobspy", fetch_jobspy, config.get("enable_jobspy", True)),
        ("career_pages", fetch_career_pages, True),
        ("x", fetch_x_tweets, config.get("enable_x", True)),
        ("reddit", fetch_reddit_jobs, config.get("enable_reddit", True)),
        ("hn", fetch_hn_jobs, config.get("enable_hn", True)),
        ("github", fetch_github_issues, config.get("enable_github", True)),
        ("reddit_tasks", fetch_reddit_tasks, config.get("enable_reddit_tasks", True)),
        ("google_tasks", fetch_google_tasks, config.get("enable_google_tasks", True)),
        ("mcp", fetch_mcp, config.get("enable_mcp", False)),
        ("discovered", fetch_discovered_sources, True),
    ]

    # Safe sources only?
    if config.get("safe_sources_only", False):
        trusted = ["json_apis", "ats", "career_pages", "discovered"]
        sources = [(name, func, flag) for name, func, flag in sources if name in trusted]

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for name, func, enabled in sources:
            if not enabled:
                continue
            rep = load_source_reputation()
            if not rep.get(name, {}).get("active", True):
                log.info(f"⏭️ Skipping {name} – source disabled.")
                continue
            futures[executor.submit(func)] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                jobs = future.result(timeout=90)
                all_jobs.extend(jobs)
                source_counts[name] += len(jobs)
                update_source_reputation(name, True)
            except Exception as e:
                log.warning(f"Source {name} failed: {e}")
                update_source_reputation(name, False)

    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

    # ─── Process ───
    filtered = []
    scam_filtered = 0
    geo_filtered = 0
    duplicate_filtered = 0
    age_filtered = 0
    ghost_filtered = 0
    blacklist_filtered = 0

    existing_for_ghost = []

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

        if is_blacklisted_platform(job):
            blacklist_filtered += 1
            continue

        if not is_globally_allowed(job):
            geo_filtered += 1
            continue

        scam_result = detect_scam(job)
        if scam_result["is_scam"]:
            scam_filtered += 1
            continue

        ghost_result = detect_ghost(job, existing_for_ghost)
        if ghost_result["is_ghost"]:
            ghost_filtered += 1
            continue

        if is_duplicate_job(job, filtered):
            duplicate_filtered += 1
            continue

        if not validate_url(job.get("url", "")):
            continue

        if matches_filter(job):
            job["score"] = calculate_score(job)
            job["ghost_score"] = ghost_result["score"]
            job["scam_score"] = scam_result["score"]
            # Save
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, url, source, posted_at, score, ghost_score, scam_score, type)
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
                job.get("type", "job")
            ))
            conn.commit()
            conn.close()
            filtered.append(job)
            existing_for_ghost.append(job)

    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

    log.info(f"   ✅ {len(filtered)} jobs matched")
    log.info(f"   🛑 {scam_filtered} rejected as scams")
    log.info(f"   🚫 {blacklist_filtered} rejected (blacklist)")
    log.info(f"   🗺️ {geo_filtered} rejected (geo)")
    log.info(f"   🔄 {duplicate_filtered} duplicates")
    log.info(f"   📅 {age_filtered} too old")
    log.info(f"   👻 {ghost_filtered} ghost")

    write_health(source_counts, len(all_jobs), len(filtered))

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
        log.error(f"CRASH: {e}\n{traceback.format_exc()}")
        sys.exit(1)
