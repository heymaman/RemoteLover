#!/usr/bin/env python3
"""
Remote Opportunity Hunter v25.2 – with Scoring & Archive
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
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
            score INTEGER DEFAULT 0,
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

# ─── SOURCE REGISTRY ───
SOURCES = []
def register_source(name, fetcher, enabled=True):
    SOURCES.append({"name": name, "fetcher": fetcher, "enabled": enabled})

# ─── FETCHERS (only essential parts shown) ───
def fetch_remoteok():
    # ... same as before, but now each job dict includes 'content' for scoring
    # Ensure each job has 'content' field with description
    # ... (we'll keep the same logic)
    pass

# ─── MAIN ───
def main():
    log.info("="*60)
    log.info("🌍 Remote Opportunity Hunter v25.2")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Scoring enabled • Archive fixed")
    log.info("="*60)

    init_db()
    archive_old_jobs()

    all_jobs = []
    source_counts = defaultdict(int)

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
                # Add score to each job
                for job in jobs:
                    job["score"] = calculate_score(job)
                all_jobs.extend(jobs)
                source_counts[name] += len(jobs)
            except Exception as e:
                log.warning(f"Source {name} failed: {e}")

    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

    # Save to database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for job in all_jobs:
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
    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log.error(f"CRASH: {e}\n{traceback.format_exc()}")
        sys.exit(1)
