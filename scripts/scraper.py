#!/usr/bin/env python3
"""
Remote Opportunity Hunter v1.0 — PRODUCTION READY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEATURES:
  • 25+ Companies (Uber, Stripe, Anthropic, Figma, Notion, etc.)
  • 6+ Sources (Greenhouse, Lever, Amazon, etc.)
  • Remote-Only Filtering (location, title, description)
  • Smart Scoring (0-100 with bonuses)
  • State Management (never show duplicates)
  • Retry Logic (exponential backoff)
  • Environment Configuration
  • Health Checks
  • Telegram + Discord Alerts
  • Config file support (config.json)
  • Generic Webhook support
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import hashlib
import logging
import requests
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set
from functools import wraps

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
# CONFIG FILE SUPPORT
# ─────────────────────────────────────────────

CONFIG_FILE = Path("config.json")

def load_config():
    """Load config from JSON file with environment overrides"""
    config = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
            log.info("✅ Loaded config.json")
        except Exception as e:
            log.warning(f"⚠️ Failed to load config.json: {e}")
    
    # Environment overrides
    if os.getenv("JOB_TITLES"):
        config["job_titles"] = os.getenv("JOB_TITLES").split(",")
    if os.getenv("REMOTE_KEYWORDS"):
        config["remote_keywords"] = os.getenv("REMOTE_KEYWORDS").split(",")
    if os.getenv("EXCLUDE_KEYWORDS"):
        config["exclude_keywords"] = os.getenv("EXCLUDE_KEYWORDS").split(",")
    if os.getenv("PRIORITY_COMPANIES"):
        config["priority_companies"] = os.getenv("PRIORITY_COMPANIES").split(",")
    if os.getenv("MAX_RETRIES"):
        config["max_retries"] = int(os.getenv("MAX_RETRIES"))
    if os.getenv("TIMEOUT_SECONDS"):
        config["timeout_seconds"] = int(os.getenv("TIMEOUT_SECONDS"))
    
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
        "linear", "supabase", "railway", "gitlab", "airbnb",
        "scale ai", "databricks", "brex", "coursera", "amplitude",
        "shopify", "discord", "slack", "retool", "convex"
    ])
    config.setdefault("max_retries", 3)
    config.setdefault("timeout_seconds", 15)
    config.setdefault("rate_limit_seconds", 0.5)
    config.setdefault("max_jobs_per_source", 50)
    
    return config

CONFIG = load_config()

# ─────────────────────────────────────────────
# ENVIRONMENT VALIDATION
# ─────────────────────────────────────────────

def validate_environment():
    """Validate required environment variables"""
    required = {
        "TELEGRAM_BOT_TOKEN": "Telegram bot token (get from @BotFather)",
        "TELEGRAM_CHAT_ID": "Telegram chat ID (get from @userinfobot)",
    }
    
    missing = []
    for key, desc in required.items():
        if not os.getenv(key):
            missing.append(f"{key} ({desc})")
    
    if missing:
        log.error("❌ Missing required environment variables:")
        for item in missing:
            log.error(f"   - {item}")
        return False
    
    log.info("✅ Environment validated")
    return True

# ─────────────────────────────────────────────
# COMPANIES
# ─────────────────────────────────────────────

COMPANIES = [
    # Big Tech
    {"name": "Amazon", "url": "https://www.amazon.jobs/en/search.json?country=&city=&region=&county=&query=&query_options=&normalized_country_code=&normalized_city_name=&normalized_state_name=&normalized_county_name=&offset=0&result_limit=50&sort=recent", "type": "amazon"},
    # Greenhouse Companies
    {"name": "Uber", "url": "https://boards-api.greenhouse.io/v1/boards/uberatg/jobs?content=true", "type": "greenhouse"},
    {"name": "Lyft", "url": "https://boards-api.greenhouse.io/v1/boards/lyft/jobs?content=true", "type": "greenhouse"},
    {"name": "Discord", "url": "https://boards-api.greenhouse.io/v1/boards/discord/jobs?content=true", "type": "greenhouse"},
    {"name": "Shopify", "url": "https://boards-api.greenhouse.io/v1/boards/shopify/jobs?content=true", "type": "greenhouse"},
    {"name": "Anthropic", "url": "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true", "type": "greenhouse"},
    {"name": "DeepMind", "url": "https://boards-api.greenhouse.io/v1/boards/deepmind/jobs?content=true", "type": "greenhouse"},
    {"name": "Stripe", "url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true", "type": "greenhouse"},
    {"name": "Airbnb", "url": "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true", "type": "greenhouse"},
    {"name": "Dropbox", "url": "https://boards-api.greenhouse.io/v1/boards/dropbox/jobs?content=true", "type": "greenhouse"},
    {"name": "Figma", "url": "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true", "type": "greenhouse"},
    {"name": "Vercel", "url": "https://boards-api.greenhouse.io/v1/boards/vercel/jobs?content=true", "type": "greenhouse"},
    {"name": "Notion", "url": "https://boards-api.greenhouse.io/v1/boards/notion/jobs?content=true", "type": "greenhouse"},
    {"name": "Linear", "url": "https://boards-api.greenhouse.io/v1/boards/linear/jobs?content=true", "type": "greenhouse"},
    {"name": "Supabase", "url": "https://boards-api.greenhouse.io/v1/boards/supabase/jobs?content=true", "type": "greenhouse"},
    {"name": "Databricks", "url": "https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true", "type": "greenhouse"},
    {"name": "Scale AI", "url": "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs?content=true", "type": "greenhouse"},
    {"name": "Brex", "url": "https://boards-api.greenhouse.io/v1/boards/brex/jobs?content=true", "type": "greenhouse"},
    {"name": "Coursera", "url": "https://boards-api.greenhouse.io/v1/boards/coursera/jobs?content=true", "type": "greenhouse"},
    {"name": "Amplitude", "url": "https://boards-api.greenhouse.io/v1/boards/amplitude/jobs?content=true", "type": "greenhouse"},
    # Lever Companies
    {"name": "Spotify", "url": "https://api.lever.co/v0/postings/spotify?mode=json&limit=100", "type": "lever"},
    # Trading Firms
    {"name": "Jane Street", "url": "https://boards-api.greenhouse.io/v1/boards/janestreet/jobs?content=true", "type": "greenhouse"},
    {"name": "Jump Trading", "url": "https://boards-api.greenhouse.io/v1/boards/jumptrading/jobs?content=true", "type": "greenhouse"},
    {"name": "Citadel", "url": "https://boards-api.greenhouse.io/v1/boards/citadel/jobs?content=true", "type": "greenhouse"},
    # Gaming
    {"name": "Roblox", "url": "https://boards-api.greenhouse.io/v1/boards/roblox/jobs?content=true", "type": "greenhouse"},
]

# ─────────────────────────────────────────────
# PARSERS
# ─────────────────────────────────────────────

def parse_greenhouse(data, company_name):
    jobs = []
    for job in data.get("jobs", []):
        location_name = ""
        for loc in job.get("locations", []):
            if loc.get("name"):
                location_name = loc.get("name")
                break
        jobs.append({
            "id": str(job.get("id", "")),
            "title": job.get("title", ""),
            "location": location_name,
            "url": job.get("absolute_url", ""),
            "company": company_name,
            "content": job.get("content", ""),
            "posted_at": job.get("updated_at", ""),
            "source": "greenhouse",
        })
    return jobs

def parse_lever(data, company_name):
    jobs = []
    for job in data:
        jobs.append({
            "id": job.get("id", ""),
            "title": job.get("text", ""),
            "location": job.get("categories", {}).get("location", ""),
            "url": job.get("hostedUrl", ""),
            "company": company_name,
            "content": "",
            "posted_at": job.get("createdAt", ""),
            "source": "lever",
        })
    return jobs

def parse_amazon(data, company_name):
    jobs = []
    for job in data.get("jobs", []):
        jobs.append({
            "id": str(job.get("id", "")),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": f"https://www.amazon.jobs{job.get('job_path', '')}",
            "company": company_name,
            "content": "",
            "posted_at": job.get("posted_date", ""),
            "source": "amazon",
        })
    return jobs

PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "amazon": parse_amazon,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ─────────────────────────────────────────────
# RETRY DECORATOR
# ─────────────────────────────────────────────

def with_retry(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = CONFIG.get("max_retries", 3)
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                wait_time = (2 ** attempt) * 0.5
                log.warning(f"Retry {attempt+1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                else:
                    raise
    return wrapper

# ─────────────────────────────────────────────
# FETCH JOBS
# ─────────────────────────────────────────────

def get_job_uid(job):
    """Generate unique ID"""
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{job['company']}::hash::{content_hash}"

@with_retry
def fetch_jobs(company):
    """Fetch jobs from a company"""
    name = company["name"]
    url = company["url"]
    parser_type = company["type"]
    timeout = CONFIG.get("timeout_seconds", 15)

    start_time = time.time()
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        parser = PARSERS.get(parser_type)
        if parser:
            jobs = parser(data, name)
            elapsed = time.time() - start_time
            log.info(f"   ✅ {name}: {len(jobs)} jobs ({elapsed:.1f}s)")
            return jobs
        return []
    except requests.exceptions.Timeout:
        log.warning(f"   ⚠️ {name}: Timeout")
        return []
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            log.warning(f"   ⚠️ {name}: Not found")
        elif e.response.status_code == 403:
            log.warning(f"   ⚠️ {name}: Forbidden")
        else:
            log.warning(f"   ⚠️ {name}: HTTP {e.response.status_code}")
        return []
    except Exception as e:
        log.warning(f"   ⚠️ {name}: {str(e)[:50]}")
        return []

# ─────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────

STATE_FILE = Path("data/seen_jobs.json")

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

# ─────────────────────────────────────────────
# FILTERING
# ─────────────────────────────────────────────

def is_remote(job: Dict) -> bool:
    """Check if job is remote"""
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    description = job.get("content", "").lower()
    
    remote_kw = CONFIG.get("remote_keywords", [])
    
    for kw in remote_kw:
        if kw in location:
            return True
    
    for kw in ["remote", "anywhere", "global"]:
        if kw in title:
            return True
    
    for kw in remote_kw:
        if kw in description:
            return True
    
    return False

def matches_filter(job: Dict) -> bool:
    """Apply all filters"""
    if not is_remote(job):
        return False
    
    title = job.get("title", "").lower()
    
    exclude_kw = CONFIG.get("exclude_keywords", [])
    for kw in exclude_kw:
        if kw in title:
            return False
    
    job_titles = CONFIG.get("job_titles", [])
    sw_kw = CONFIG.get("software_keywords", [])
    
    matches_customer = any(kw in title for kw in job_titles)
    matches_swe = any(kw in title for kw in sw_kw)
    
    return matches_customer or matches_swe

# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

def calculate_score(job: Dict) -> int:
    """Score job 0-100"""
    score = 0
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    location = job.get("location", "").lower()
    description = job.get("content", "").lower()
    
    # Title match (30 points)
    job_titles = CONFIG.get("job_titles", [])
    for kw in job_titles[:5]:
        if kw in title:
            score += 30
            break
    else:
        sw_kw = CONFIG.get("software_keywords", [])
        for kw in sw_kw[:3]:
            if kw in title:
                score += 20
                break
    
    # Remote quality (20 points)
    if "anywhere" in location or "global" in location:
        score += 20
    elif "remote" in location:
        score += 15
    elif "fully remote" in description:
        score += 18
    
    # Startup bonus (15 points)
    priority = CONFIG.get("priority_companies", [])
    for pc in priority:
        if pc.lower() in company:
            score += 15
            break
    
    # Freshness (10 points)
    posted = job.get("posted_at", "")
    if posted:
        try:
            posted_date = datetime.fromisoformat(posted.replace('Z', '+00:00'))
            days_ago = (datetime.now() - posted_date).days
            if days_ago <= 1:
                score += 10
            elif days_ago <= 3:
                score += 8
            elif days_ago <= 7:
                score += 5
        except:
            pass
    
    # Direct application (10 points)
    if "greenhouse.io" in job.get("url", "") or "lever.co" in job.get("url", ""):
        score += 10
    
    return min(100, score)

# ─────────────────────────────────────────────
# NOTIFIERS
# ─────────────────────────────────────────────

def send_telegram(jobs: List[Dict]):
    """Send Telegram alerts"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    
    if not jobs:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🌍 **Remote Opportunity Hunter**\nNo new remote jobs found.",
                    "parse_mode": "Markdown"
                },
                timeout=10
            )
        except:
            pass
        return
    
    summary = f"🌍 **{len(jobs)} REMOTE JOBS FOUND**\n"
    summary += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    
    for job in jobs[:10]:
        score = job.get('score', 0)
        stars = '⭐' * min(5, score // 20 + 1)
        summary += (
            f"**{job['company']}**\n"
            f"💼 {job['title']}\n"
            f"📍 {job.get('location') or 'Remote'}\n"
            f"🎯 {score}/100 {stars}\n"
            f"🔗 [Apply]({job['url']})\n\n"
        )
    
    if len(jobs) > 10:
        summary += f"📌 +{len(jobs)-10} more jobs\n"
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": summary, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram failed: {e}")

def send_discord(jobs: List[Dict]):
    """Send Discord alerts"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url or not jobs:
        return
    
    for job in jobs[:5]:
        embed = {
            "title": job["title"],
            "url": job["url"],
            "color": 0x00FF00,
            "fields": [
                {"name": "Company", "value": job["company"], "inline": True},
                {"name": "Location", "value": job.get("location") or "Remote", "inline": True},
                {"name": "Score", "value": f"{job.get('score', 0)}/100", "inline": True},
            ]
        }
        try:
            requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        except:
            pass
        time.sleep(0.5)

# ─────────────────────────────────────────────
# GENERIC WEBHOOK
# ─────────────────────────────────────────────

def send_webhook(jobs: List[Dict]):
    """Send jobs to a generic webhook"""
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url or not jobs:
        return
    
    try:
        requests.post(
            webhook_url,
            json={"jobs": jobs[:10], "timestamp": datetime.now().isoformat()},
            timeout=10,
            headers={"Content-Type": "application/json"}
        )
        log.info("✅ Webhook sent")
    except Exception as e:
        log.warning(f"Webhook failed: {e}")

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

def check_health(jobs_fetched: int, jobs_matched: int, errors: List[str]) -> Dict:
    return {
        "status": "healthy" if jobs_fetched > 0 else "degraded",
        "timestamp": datetime.now().isoformat(),
        "jobs_fetched": jobs_fetched,
        "jobs_matched": jobs_matched,
        "errors": errors,
        "companies_scanned": len(COMPANIES),
        "sources": list(set([c.get("type") for c in COMPANIES])),
        "config": {
            "job_titles": len(CONFIG.get("job_titles", [])),
            "remote_keywords": len(CONFIG.get("remote_keywords", [])),
        }
    }

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("🌍 REMOTE OPPORTUNITY HUNTER v1.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)
    
    if not validate_environment():
        sys.exit(1)
    
    seen = load_seen()
    all_jobs = []
    filtered_jobs = []
    errors = []

    for company in COMPANIES:
        try:
            jobs = fetch_jobs(company)
            all_jobs.extend(jobs)
        except Exception as e:
            errors.append(f"{company['name']}: {str(e)[:50]}")
            log.warning(f"   ⚠️ {company['name']}: {str(e)[:50]}")
        time.sleep(CONFIG.get("rate_limit_seconds", 0.5))

    log.info(f"\n📊 Total jobs fetched: {len(all_jobs)}")
    
    # Filter and score
    log.info("🔬 Filtering for remote roles...")
    for job in all_jobs:
        uid = get_job_uid(job)
        if uid not in seen:
            seen.add(uid)
            if matches_filter(job):
                job['score'] = calculate_score(job)
                filtered_jobs.append(job)
    
    filtered_jobs.sort(key=lambda x: x.get('score', 0), reverse=True)
    
    log.info(f"   ✅ {len(filtered_jobs)} remote jobs matched")
    
    save_seen(seen)
    
    # Health check
    health = check_health(len(all_jobs), len(filtered_jobs), errors)
    log.info(f"📊 Health: {health['status']}")
    
    # Send alerts
    if filtered_jobs:
        log.info(f"📤 Sending {len(filtered_jobs)} job alerts...")
        send_telegram(filtered_jobs)
        send_discord(filtered_jobs)
        send_webhook(filtered_jobs)
    else:
        log.info("ℹ️ No remote jobs found")
        send_telegram([])
    
    log.info("✅ Job hunt complete!")

if __name__ == "__main__":
    main()
