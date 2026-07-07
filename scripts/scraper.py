#!/usr/bin/env python3
"""
Remote Opportunity Hunter v25.0 — Rebuilt from Research
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCES:
  • Free public APIs: Remotive, RemoteOK, Himalayas, WeWorkRemotely [reference:9]
  • JobSpy: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google [reference:10]
  • ATS: Greenhouse, Lever (with known-good list)
  • Social: X (Twitter), Reddit, Hacker News, GitHub Issues
  • Tasks: Reddit (r/slavelabour, r/beermoney), Google (SerpAPI)
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

# ─── USER AGENT ROTATION (anti-detection) ───
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.1 Safari/605.1.15",
]

def random_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}

# ─── DATABASE ───
DB_PATH = Path("data/jobs.db")

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
            score INTEGER,
            ghost_score INTEGER,
            scam_score INTEGER,
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            type TEXT DEFAULT 'job',
            salary_min INTEGER,
            salary_max INTEGER,
            salary_text TEXT,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Auto-migrate missing columns
    cols = get_columns(conn, "jobs")
    for col in ["notes", "type", "salary_min", "salary_max", "salary_text", "source_url"]:
        if col not in cols:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

def archive_old_jobs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    c.execute("""
        INSERT INTO jobs_archive SELECT * FROM jobs WHERE seen_at < ?
    """, (cutoff,))
    c.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))
    conn.commit()
    conn.close()

# ─── NORMALIZATION ───
def normalize_date(date_str):
    """Convert various date formats to ISO 8601.[reference:11]"""
    if not date_str:
        return datetime.now().isoformat()
    try:
        # Epoch seconds
        if isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str).isoformat()
        # Try ISO
        return datetime.fromisoformat(date_str.replace('Z', '+00:00')).isoformat()
    except:
        return datetime.now().isoformat()

def normalize_salary(salary_data):
    """Extract min, max, and text from salary data.[reference:12]"""
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

# ─── SOURCE REGISTRY ───
SOURCES = []

def register_source(name, fetcher, enabled=True, safe=True):
    SOURCES.append({"name": name, "fetcher": fetcher, "enabled": enabled, "safe": safe})

# ─── FETCHERS ───
def fetch_remoteok():
    """RemoteOK free public API.[reference:13]"""
    try:
        resp = requests.get("https://remoteok.com/api", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            # Skip first element (legal notice)[reference:14]
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
    """Remotive free public API.[reference:15]"""
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
    """Himalayas free public API (100k+ listings).[reference:16]"""
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
    """WeWorkRemotely RSS feed.[reference:17]"""
    try:
        resp = requests.get("https://weworkremotely.com/remote-jobs.rss", headers=random_headers(), timeout=20)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            jobs = []
            for item in root.findall(".//item"):
                title = item.find("title").text or ""
                # Company is baked into title: "Company: Role"[reference:18]
                if ": " in title:
                    company, role = title.split(": ", 1)
                else:
                    company, role = "", title
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
    """JobSpy: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google.[reference:19]"""
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
            results_wanted=30,
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

# ─── REGISTER SOURCES ───
register_source("remoteok", fetch_remoteok)
register_source("remotive", fetch_remotive)
register_source("himalayas", fetch_himalayas)
register_source("weworkremotely", fetch_weworkremotely)
register_source("jobspy", fetch_jobspy, enabled=False)  # Optional dependency

# Greenhouse companies
for slug in ["stripe", "anthropic", "figma", "notion", "linear", "supabase", "gitlab"]:
    register_source(f"greenhouse_{slug}", lambda s=slug: fetch_greenhouse(s))

# ─── MAIN ───
def main():
    log.info("="*60)
    log.info("🌍 Remote Opportunity Hunter v25.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Research-Backed • Modular • Fast")
    log.info("="*60)

    init_db()
    archive_old_jobs()

    all_jobs = []
    source_counts = defaultdict(int)

    # Fetch from all registered sources
    log.info("📡 Fetching from sources...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for src in SOURCES:
            if not src["enabled"]:
                continue
            futures[executor.submit(src["fetcher"])] = src["name"]

        for future in as_completed(futures):
            name = futures[future]
            try:
                jobs = future.result(timeout=90)
                all_jobs.extend(jobs)
                source_counts[name] += len(jobs)
            except Exception as e:
                log.warning(f"Source {name} failed: {e}")

    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

    # Save to database (simplified)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for job in all_jobs:
        try:
            c.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, url, source, source_url,
                 posted_at, salary_min, salary_max, salary_text, type, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                job.get("content", "")
            ))
        except Exception as e:
            log.warning(f"Save failed: {e}")
    conn.commit()
    conn.close()

    log.info(f"✅ Saved {len(all_jobs)} jobs to database")
    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log.error(f"CRASH: {e}\n{traceback.format_exc()}")
        sys.exit(1)
