#!/usr/bin/env python3
"""
Remote Lover v19.0 – ULTIMATE (COMPLETE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All fetchers implemented, source discovery added, email digest ready.
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

# ─── USER AGENT ROTATION ───
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]
def random_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}

# ─── FETCH WITH RETRY ───
def fetch_with_retry(url, headers=None, timeout=10, retries=3):
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
        except:
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

# Environment overrides
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
config.setdefault("enable_semantic_scoring", False)
config.setdefault("enable_source_discovery", True)
config.setdefault("enable_google_search", True)
config.setdefault("enable_email_digest", False)

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
SOURCE_REPUTATION_FILE = Path("data/source_reputation.json")

def get_columns(conn, table_name):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in c.fetchall()]

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
    cols = get_columns(conn, "jobs")
    if "saved" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN saved BOOLEAN DEFAULT 0")
    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

def archive_old_jobs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    c.execute("INSERT INTO jobs_archive SELECT * FROM jobs WHERE seen_at < ?", (cutoff,))
    c.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))
    conn.commit()
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

# ─── SEMANTIC SCORING ───
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    HAS_SEMANTIC = True
    MODEL = SentenceTransformer('all-MiniLM-L6-v2')
except:
    HAS_SEMANTIC = False
    MODEL = None

def semantic_score(job, profile_text=""):
    if not HAS_SEMANTIC or not config.get("enable_semantic_scoring", False) or not profile_text:
        return 0
    desc = job.get("content", "")[:512]
    if not desc:
        return 0
    emb_desc = MODEL.encode(desc)
    emb_profile = MODEL.encode(profile_text[:512])
    sim = np.dot(emb_desc, emb_profile) / (np.linalg.norm(emb_desc) * np.linalg.norm(emb_profile))
    return int(sim * 100)

# ─── SCORING ───
def calculate_score(job, profile_text=""):
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

    # Semantic
    if config.get("enable_semantic_scoring", False) and profile_text:
        sem_score = semantic_score(job, profile_text)
        score += sem_score * 0.3

    return max(0, min(100, int(score)))

# ─── SOURCE REPUTATION ───
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

def fetch_remoteok():
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

def fetch_greenhouse(slug):
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
    if not api_key or not config.get("enable_google_search", True):
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
    # Y Combinator Jobs – use the public JSON endpoint
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
    # Wellfound (AngelList) – scrape the roles page
    try:
        # Wellfound is JS-heavy; we'll use a simple HTML parser with BeautifulSoup if available.
        try:
            from bs4 import BeautifulSoup
            HAS_BS4 = True
        except ImportError:
            HAS_BS4 = False
            log.warning("   ⚠️ BeautifulSoup not installed. Wellfound skipped.")
            return []
        resp = requests.get("https://wellfound.com/roles", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            jobs = []
            # Find job cards – this is a simplified example; actual selectors may change.
            for card in soup.select(".role-card"):
                title_elem = card.select_one(".role-title")
                company_elem = card.select_one(".company-name")
                link_elem = card.select_one("a")
                if title_elem and company_elem and link_elem:
                    jobs.append({
                        "id": f"wf_{hashlib.md5(link_elem.get('href', '').encode()).hexdigest()[:8]}",
                        "title": title_elem.text.strip(),
                        "company": company_elem.text.strip(),
                        "location": "Remote" if "Remote" in card.text else "On-site",
                        "url": link_elem.get("href"),
                        "source": "wellfound",
                        "source_url": "https://wellfound.com/roles",
                        "posted_at": datetime.now().isoformat(),
                        "salary_min": None,
                        "salary_max": None,
                        "salary_text": "",
                        "type": "job",
                        "content": ""
                    })
            log.info(f"   ✅ Wellfound: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Wellfound failed: {e}")
    return []

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
                            "source": f"discovered_{src['name'][:10]}",
                            "source_url": src["url"],
                            "posted_at": normalize_date(item.get("date", item.get("posted_at", ""))),
                            "salary_min": None,
                            "salary_max": None,
                            "salary_text": "",
                            "type": "job",
                            "content": item.get("description", item.get("content", ""))
                        })
            elif src["type"] == "rss" and data:
                root = ET.fromstring(data)
                for item in root.findall(".//item"):
                    jobs.append({
                        "id": item.find("link").text if item.find("link") is not None else "",
                        "title": item.find("title").text if item.find("title") is not None else "",
                        "company": src["name"],
                        "location": "Remote",
                        "url": item.find("link").text if item.find("link") is not None else "",
                        "source": f"discovered_{src['name'][:10]}",
                        "source_url": src["url"],
                        "posted_at": normalize_date(item.find("pubDate").text if item.find("pubDate") is not None else ""),
                        "salary_min": None,
                        "salary_max": None,
                        "salary_text": "",
                        "type": "job",
                        "content": item.find("description").text if item.find("description") is not None else ""
                    })
        except Exception as e:
            log.warning(f"   ⚠️ Discovered source {src['name']} failed: {e}")
    log.info(f"   ✅ Discovered sources: {len(jobs)} jobs")
    return jobs

# ─── SOURCE DISCOVERY (weekly) ───
def discover_new_sources():
    if not config.get("enable_source_discovery", True):
        return
    log.info("📡 Running source discovery...")
    new_sources = []
    discovered_urls = set()

    # 1. Known directories
    directories = [
        "https://www.remotejobboards.com",
        "https://jobboardsearch.com",
        "https://www.jobboardfinder.com",
    ]
    for dir_url in directories:
        try:
            data = fetch_with_retry(dir_url, timeout=10)
            if data:
                links = re.findall(r'href=["\'](https?://[^"\']+)["\']', data)
                for link in links:
                    if "job" in link or "board" in link or "career" in link:
                        if link not in discovered_urls:
                            discovered_urls.add(link)
                            new_sources.append({"name": link.split("/")[2], "url": link, "type": "html"})
        except Exception as e:
            log.warning(f"Directory scan failed: {e}")

    # 2. SerpAPI (Google search)
    api_key = os.getenv("SERPAPI_KEY")
    if api_key:
        queries = ["new remote job board", "best remote job boards 2025", "alternative to LinkedIn jobs"]
        for q in queries:
            try:
                resp = requests.get("https://serpapi.com/search", params={"q": q, "api_key": api_key, "num": 10}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for result in data.get("organic_results", []):
                        url = result.get("link")
                        if url and "job" in url and url not in discovered_urls:
                            discovered_urls.add(url)
                            new_sources.append({"name": result.get("title", url)[:50], "url": url, "type": "html"})
            except Exception as e:
                log.warning(f"SerpAPI search failed: {e}")

    # 3. Add to sources table
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for src in new_sources:
        c.execute("SELECT id FROM sources WHERE url = ?", (src["url"],))
        if not c.fetchone():
            c.execute("""
                INSERT INTO sources (name, url, type, discovered_at, last_checked, active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (src["name"], src["url"], src["type"], datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    log.info(f"✅ Discovered {len(new_sources)} new sources")

# ─── EMAIL DIGEST ───
def send_daily_digest(jobs):
    if not config.get("enable_email_digest", False):
        return
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", 587))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to_email = os.getenv("EMAIL_TO")
    if not all([host, port, user, password, to_email]):
        log.warning("Email not configured – skipping digest.")
        return
    if not jobs:
        return
    top_jobs = jobs[:10]
    body = "🌍 Remote Jobs Digest – Top 10\n\n"
    for job in top_jobs:
        body += f"🏢 {job['company']} – {job['title']}\n📍 {job.get('location', 'Remote')}\n⭐ Score: {job['score']}\n🔗 {job['url']}\n\n"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Remote Jobs Digest – {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = user
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, to_email, msg.as_string())
        log.info("✅ Daily digest sent.")
    except Exception as e:
        log.warning(f"Email digest failed: {e}")

# ─── MAIN ───
def main():
    log.info("="*60)
    log.info("🌍 Remote Opportunity Hunter v29.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Ultimate – All Sources • Self‑Expanding")
    log.info("="*60)

    init_db()
    archive_old_jobs()

    # Weekly discovery
    if datetime.now().weekday() == 0:
        discover_new_sources()

    all_jobs = []
    source_counts = defaultdict(int)

    # ─── Build source list ───
    sources = [
        ("remoteok", fetch_remoteok, True),
        ("remotive", fetch_remotive, True),
        ("himalayas", fetch_himalayas, True),
        ("weworkremotely", fetch_weworkremotely, True),
        ("jobspy", fetch_jobspy, config.get("enable_jobspy", True)),
        ("x", fetch_x_tweets, config.get("enable_x", True)),
        ("reddit", fetch_reddit_jobs, config.get("enable_reddit", True)),
        ("hn", fetch_hn_jobs, config.get("enable_hn", True)),
        ("github", fetch_github_issues, config.get("enable_github", True)),
        ("reddit_tasks", fetch_reddit_tasks, config.get("enable_reddit_tasks", True)),
        ("google_search", fetch_google_jobs, config.get("enable_google_search", True)),
        ("discovered", fetch_discovered_sources, True),
        ("yc", fetch_yc_jobs, True),
        ("wellfound", fetch_wellfound, True),
    ]
    # Greenhouse
    for slug in ["stripe", "anthropic", "figma", "notion", "linear", "supabase", "gitlab"]:
        sources.append((f"greenhouse_{slug}", lambda s=slug: fetch_greenhouse(s), True))

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for name, fetcher, enabled in sources:
            if not enabled:
                continue
            rep = load_source_reputation()
            if not rep.get(name, {}).get("active", True):
                continue
            futures[executor.submit(fetcher)] = name

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

    # ─── Process jobs ───
    profile_text = os.getenv("PROFILE_TEXT", "")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for job in all_jobs:
        job["score"] = calculate_score(job, profile_text)
        try:
            c.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, url, source, source_url,
                 posted_at, salary_min, salary_max, salary_text, type, content, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.get("id", ""),
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("url", ""),
                job.get("source", ""),
                job.get("source_url", ""),
                job.get("posted_at", ""),
                job.get("salary_min"),
                job.get("salary_max"),
                job.get("salary_text", ""),
                job.get("type", "job"),
                job.get("content", ""),
                job.get("score", 0)
            ))
        except Exception as e:
            log.warning(f"Save failed: {e}")
    conn.commit()
    conn.close()

    log.info(f"✅ Saved {len(all_jobs)} jobs to database")

    # ─── Email digest ───
    if config.get("enable_email_digest", False):
        send_daily_digest(all_jobs[:10])

    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log.error(f"CRASH: {e}\n{traceback.format_exc()}")
        sys.exit(1)
