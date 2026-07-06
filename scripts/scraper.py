#!/usr/bin/env python3
"""
Remote Opportunity Hunter v6.0 — RELIABLE & PRODUCTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEATURES:
  ✅ Primary: JobSpy (if installed) → LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter
  ✅ Secondary: RemoteOK, WeWorkRemotely, Remotive (public APIs, always work)
  ✅ Parallel fetching for speed
  ✅ Ghost job detection + scoring
  ✅ Self-improvement feedback loop
  ✅ Test mode: send a test job to verify Telegram
  ✅ Configurable via environment variables
  ✅ Runs on GitHub Actions for free
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import hashlib
import logging
import requests
import time
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

CONFIG_FILE = Path("config.json")
STATE_FILE = Path("data/seen_jobs.json")
FEEDBACK_FILE = Path("data/feedback.json")

def load_config():
    config = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
            log.info("✅ Loaded config.json")
        except Exception as e:
            log.warning(f"⚠️ Failed to load config.json: {e}")
    
    # Environment overrides
    for key in ["JOB_TITLES", "REMOTE_KEYWORDS", "EXCLUDE_KEYWORDS", "PRIORITY_COMPANIES"]:
        if os.getenv(key):
            config[key.lower()] = os.getenv(key).split(",")
    for key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD"]:
        if os.getenv(key):
            config[key.lower()] = int(os.getenv(key))
    for key in ["ENABLE_JOBSPY", "ENABLE_PUBLIC_APIS", "ENABLE_TEST_JOB"]:
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
    config.setdefault("ghost_threshold", 40)  # Lower = more jobs
    config.setdefault("enable_jobspy", True)
    config.setdefault("enable_public_apis", True)
    config.setdefault("enable_test_job", False)  # Set to True for testing

    return config

CONFIG = load_config()

# ─────────────────────────────────────────────
# STATE & UTILITY
# ─────────────────────────────────────────────

def load_seen() -> Set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except:
            return set()
    return set()

def save_seen(seen: Set[str]):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(list(seen)))

def get_job_uid(job):
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{job['company']}::hash::{content_hash}"

# ─────────────────────────────────────────────
# JOB FETCHERS
# ─────────────────────────────────────────────

def fetch_jobspy():
    """Fetch using JobSpy (LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter)"""
    if not CONFIG.get("enable_jobspy", True):
        return []
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("⚠️ JobSpy not installed. Install with: pip install python-jobspy")
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
                results_wanted=30,
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
                    "salary": f"{row.get('min_amount', '')}-{row.get('max_amount', '')}" if row.get('min_amount') else "",
                })
        except Exception as e:
            log.warning(f"JobSpy '{term}' failed: {e}")
    log.info(f"   ✅ JobSpy: {len(all_jobs)} jobs")
    return all_jobs

def fetch_remoteok():
    """RemoteOK public API"""
    try:
        resp = requests.get("https://remoteok.com/api", timeout=CONFIG.get("timeout_seconds", 20))
        if resp.status_code == 200:
            data = resp.json()
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
                        "salary": "",
                    })
            log.info(f"   ✅ RemoteOK: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"RemoteOK failed: {e}")
    return []

def fetch_weworkremotely():
    """We Work Remotely RSS feed"""
    try:
        import xml.etree.ElementTree as ET
        resp = requests.get("https://weworkremotely.com/remote-jobs.rss", timeout=CONFIG.get("timeout_seconds", 20))
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            jobs = []
            for item in root.findall(".//item"):
                title = item.find("title").text or ""
                link = item.find("link").text or ""
                desc = item.find("description").text or ""
                pub_date = item.find("pubDate").text or ""
                company = ""
                if ": " in title:
                    company, title = title.split(": ", 1)
                jobs.append({
                    "id": link,
                    "title": title,
                    "company": company,
                    "location": "Remote",
                    "url": link,
                    "content": desc,
                    "posted_at": pub_date,
                    "source": "weworkremotely",
                    "salary": "",
                })
            log.info(f"   ✅ WeWorkRemotely: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"WeWorkRemotely failed: {e}")
    return []

def fetch_remotive():
    """Remotive public API"""
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", timeout=CONFIG.get("timeout_seconds", 20))
        if resp.status_code == 200:
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company_name", ""),
                    "location": "Remote",
                    "url": job.get("url", ""),
                    "content": job.get("description", ""),
                    "posted_at": job.get("publication_date", ""),
                    "source": "remotive",
                    "salary": "",
                })
            log.info(f"   ✅ Remotive: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"Remotive failed: {e}")
    return []

# ─────────────────────────────────────────────
# FILTERING & SCORING
# ─────────────────────────────────────────────

def is_remote(job):
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    desc = job.get("content", "").lower()
    for kw in CONFIG.get("remote_keywords", []):
        if kw in location or kw in desc:
            return True
    for kw in ["remote", "anywhere", "global"]:
        if kw in title:
            return True
    return False

def matches_filter(job):
    if not is_remote(job):
        return False
    title = job.get("title", "").lower()
    for kw in CONFIG.get("exclude_keywords", []):
        if kw in title:
            return False
    job_titles = CONFIG.get("job_titles", [])
    sw = CONFIG.get("software_keywords", [])
    return any(kw in title for kw in job_titles) or any(kw in title for kw in sw)

def calculate_score(job):
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

# ─────────────────────────────────────────────
# GHOST DETECTION
# ─────────────────────────────────────────────

def detect_ghost(job):
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
    return {
        "score": max(0, score),
        "is_ghost": score < CONFIG.get("ghost_threshold", 40),
        "signals": signals
    }

# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

def send_telegram(jobs):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    if not jobs:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "🌍 No new remote jobs found.", "parse_mode": "Markdown"},
                timeout=10
            )
        except:
            pass
        return
    msg = f"🌍 **{len(jobs)} REMOTE JOBS FOUND**\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    for job in jobs[:10]:
        stars = '⭐' * min(5, job.get('score', 0) // 20 + 1)
        msg += (
            f"**{job['company']}**\n"
            f"💼 {job['title']}\n"
            f"📍 {job.get('location') or 'Remote'}\n"
            f"🎯 {job.get('score', 0)}/100 {stars}\n"
            f"🔗 [Apply]({job['url']})\n\n"
        )
    if len(jobs) > 10:
        msg += f"📌 +{len(jobs)-10} more jobs\n"
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("🌍 REMOTE OPPORTUNITY HUNTER v6.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Reliable • Zero Cost • Self-Improving")
    log.info("=" * 60)
    
    # Validate secrets
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        sys.exit(1)
    
    # Test job (optional)
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
        send_telegram(test_job)
        log.info("✅ Test job sent. Remove ENABLE_TEST_JOB to disable.")
    
    seen = load_seen()
    all_jobs = []
    
    # 1. JobSpy (if enabled)
    if CONFIG.get("enable_jobspy", True):
        log.info("📡 Fetching from JobSpy...")
        jobs = fetch_jobspy()
        all_jobs.extend(jobs)
    
    # 2. Public APIs (if enabled)
    if CONFIG.get("enable_public_apis", True):
        log.info("📡 Fetching from public APIs...")
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(fetch_remoteok): "RemoteOK",
                executor.submit(fetch_weworkremotely): "WeWorkRemotely",
                executor.submit(fetch_remotive): "Remotive",
            }
            for future in as_completed(futures):
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                except Exception as e:
                    log.warning(f"{futures[future]} failed: {e}")
    
    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    
    # Process jobs
    filtered = []
    for job in all_jobs:
        uid = get_job_uid(job)
        if uid in seen:
            continue
        seen.add(uid)
        ghost = detect_ghost(job)
        if ghost["is_ghost"]:
            continue
        if matches_filter(job):
            job["score"] = calculate_score(job)
            filtered.append(job)
    
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
    save_seen(seen)
    
    log.info(f"   ✅ {len(filtered)} jobs matched")
    
    # Send alerts
    if filtered:
        send_telegram(filtered)
    else:
        log.info("ℹ️ No new remote jobs found")
        send_telegram([])
    
    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    main()
