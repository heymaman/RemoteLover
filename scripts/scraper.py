#!/usr/bin/env python3
"""
Remote Opportunity Hunter v7.0 — ULTIMATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEATURES:
  • Multi‑source: MCP (if running), public APIs, JobSpy (if installed)
  • User‑defined custom job boards via config.json
  • Adaptive retries with exponential backoff + jitter
  • Source performance tracking (enables self‑optimisation)
  • Ghost job detection (stale / missing data)
  • Smart scoring (0‑100) with customizable weights
  • Test mode – send a test job to verify Telegram
  • Parallel fetching for speed
  • Zero cost, runs on GitHub Actions
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
from typing import List, Dict, Set, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

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
PERFORMANCE_FILE = Path("data/source_performance.json")

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
            config[key.lower()] = [x.strip() for x in os.getenv(key).split(",") if x.strip()]
    for key in ["MAX_RETRIES", "TIMEOUT_SECONDS", "GHOST_THRESHOLD"]:
        if os.getenv(key):
            config[key.lower()] = int(os.getenv(key))
    for key in ["ENABLE_JOBSPY", "ENABLE_PUBLIC_APIS", "ENABLE_MCP", "ENABLE_TEST_JOB"]:
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
    config.setdefault("ghost_threshold", 40)          # Lower = more jobs
    config.setdefault("enable_mcp", True)
    config.setdefault("enable_public_apis", True)
    config.setdefault("enable_jobspy", True)
    config.setdefault("enable_test_job", False)
    config.setdefault("mcp_url", os.getenv("MCP_API_URL", "http://localhost:3000/search"))

    # Custom job boards (user can add any JSON/RSS feed)
    config.setdefault("custom_boards", [])   # list of {"name": "...", "url": "...", "type": "json"|"rss"}

    return config

CONFIG = load_config()

# ─────────────────────────────────────────────
# STATE & PERFORMANCE TRACKING
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

def load_performance() -> Dict:
    if PERFORMANCE_FILE.exists():
        try:
            return json.loads(PERFORMANCE_FILE.read_text())
        except:
            return {}
    return {}

def save_performance(perf: Dict):
    PERFORMANCE_FILE.parent.mkdir(exist_ok=True)
    PERFORMANCE_FILE.write_text(json.dumps(perf, indent=2))

def get_job_uid(job):
    job_id = job.get("id", "").strip()
    if job_id:
        return f"{job['company']}::{job_id}"
    content = f"{job['company']}::{job['title']}::{job.get('location', '')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{job['company']}::hash::{content_hash}"

# ─────────────────────────────────────────────
# ADAPTIVE RETRY WITH BACKOFF
# ─────────────────────────────────────────────

def fetch_with_retry(url: str, method: str = "GET", json_data: dict = None,
                     headers: dict = None, timeout: int = 20, retries: int = 3) -> Optional[dict]:
    """Fetch a URL with exponential backoff + jitter."""
    for attempt in range(retries):
        try:
            if method.upper() == "POST":
                resp = requests.post(url, json=json_data, headers=headers, timeout=timeout)
            else:
                resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json() if "application/json" in resp.headers.get("Content-Type", "") else resp.text
            elif resp.status_code == 429:
                wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                time.sleep(wait)
                continue
            else:
                log.warning(f"HTTP {resp.status_code} from {url}")
                return None
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = (2 ** attempt) * 0.5 + random.uniform(0, 0.5)
            time.sleep(wait)
    return None

# ─────────────────────────────────────────────
# SOURCE FETCHERS
# ─────────────────────────────────────────────

def fetch_mcp() -> List[Dict]:
    """Pre‑built MCP server."""
    if not CONFIG.get("enable_mcp", True):
        return []
    url = CONFIG.get("mcp_url", "http://localhost:3000/search")
    try:
        data = fetch_with_retry(url, method="POST", json_data={"query": "remote customer support", "limit": 50},
                                headers={"Content-Type": "application/json"},
                                timeout=10, retries=2)
        if data and "jobs" in data:
            jobs = data["jobs"]
            normalized = []
            for job in jobs:
                normalized.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "company": job.get("company", job.get("company_name", "")),
                    "location": job.get("location", "Remote"),
                    "url": job.get("url", job.get("apply_url", "")),
                    "content": job.get("description", job.get("content", "")),
                    "posted_at": job.get("posted_at", job.get("date", "")),
                    "source": "mcp",
                    "salary": job.get("salary", ""),
                })
            log.info(f"   ✅ MCP: {len(normalized)} jobs")
            return normalized
    except Exception as e:
        log.warning(f"   ⚠️ MCP request failed: {e}")
    return []

# ─── Public APIs ──────────────────────────────

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
                        "salary": "",
                    })
            log.info(f"   ✅ RemoteOK: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ RemoteOK failed: {e}")
    return []

def fetch_weworkremotely() -> List[Dict]:
    try:
        import xml.etree.ElementTree as ET
        text = fetch_with_retry("https://weworkremotely.com/remote-jobs.rss", timeout=CONFIG["timeout_seconds"])
        if text:
            root = ET.fromstring(text)
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
        log.warning(f"   ⚠️ WeWorkRemotely failed: {e}")
    return []

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
                    "salary": "",
                })
            log.info(f"   ✅ Remotive: {len(jobs)} jobs")
            return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Remotive failed: {e}")
    return []

def fetch_public_apis() -> List[Dict]:
    if not CONFIG.get("enable_public_apis", True):
        return []
    all_jobs = []
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
    return all_jobs

# ─── JobSpy (optional) ─────────────────────────

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
            log.warning(f"   ⚠️ JobSpy '{term}' failed: {e}")
    log.info(f"   ✅ JobSpy: {len(all_jobs)} jobs")
    return all_jobs

# ─── Custom boards (user‑defined) ─────────────

def fetch_custom_board(board: dict) -> List[Dict]:
    name = board.get("name", "custom")
    url = board.get("url")
    board_type = board.get("type", "json")
    try:
        if board_type == "json":
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
                            "source": f"custom_{name}",
                            "salary": item.get("salary", ""),
                        })
                log.info(f"   ✅ Custom '{name}': {len(jobs)} jobs")
                return jobs
        elif board_type == "rss":
            import xml.etree.ElementTree as ET
            text = fetch_with_retry(url, timeout=CONFIG["timeout_seconds"])
            if text:
                root = ET.fromstring(text)
                jobs = []
                for item in root.findall(".//item"):
                    title = item.find("title").text or ""
                    link = item.find("link").text or ""
                    desc = item.find("description").text or ""
                    pub_date = item.find("pubDate").text or ""
                    company = item.find("company") or item.find("creator")
                    company = company.text if company is not None else ""
                    jobs.append({
                        "id": link,
                        "title": title,
                        "company": company,
                        "location": "Remote",
                        "url": link,
                        "content": desc,
                        "posted_at": pub_date,
                        "source": f"custom_{name}",
                        "salary": "",
                    })
                log.info(f"   ✅ Custom RSS '{name}': {len(jobs)} jobs")
                return jobs
    except Exception as e:
        log.warning(f"   ⚠️ Custom board '{name}' failed: {e}")
    return []

# ─────────────────────────────────────────────
# FILTERING, SCORING, GHOST DETECTION
# ─────────────────────────────────────────────

def is_remote(job: Dict) -> bool:
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

def matches_filter(job: Dict) -> bool:
    if not is_remote(job):
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

    # 1. Title relevance
    for kw in CONFIG.get("job_titles", [])[:5]:
        if kw in title:
            score += 30
            break
    else:
        for kw in CONFIG.get("software_keywords", [])[:3]:
            if kw in title:
                score += 20
                break

    # 2. Remote quality
    if "anywhere" in location or "global" in location:
        score += 20
    elif "remote" in location:
        score += 15
    elif "fully remote" in desc:
        score += 18

    # 3. Startup/priority company bonus
    for pc in CONFIG.get("priority_companies", []):
        if pc.lower() in company:
            score += 15
            break

    # 4. Freshness
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

    # 5. Direct application (preferred)
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
    # Check for suspicious salary
    salary = job.get("salary", "")
    if salary and "unpaid" in salary.lower():
        score -= 15
        signals.append("unpaid")
    return {
        "score": max(0, score),
        "is_ghost": score < CONFIG.get("ghost_threshold", 40),
        "signals": signals
    }

# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

def send_telegram(jobs: List[Dict], is_test: bool = False):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("⚠️ Telegram secrets missing")
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
            f"📡 {job.get('source', 'unknown').upper()}\n"
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
        log.info(f"✅ Telegram sent ({len(jobs)} jobs)")
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("🌍 REMOTE OPPORTUNITY HUNTER v7.0")
    log.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("   Multi‑Source • Adaptive • Self‑Tuning")
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
        send_telegram(test_job, is_test=True)
        log.info("✅ Test job sent. Set ENABLE_TEST_JOB=false to disable.")

    seen = load_seen()
    all_jobs = []
    source_counts = defaultdict(int)

    # 1. MCP (pre‑built)
    log.info("📡 Fetching from MCP...")
    mcp_jobs = fetch_mcp()
    all_jobs.extend(mcp_jobs)
    source_counts["mcp"] += len(mcp_jobs)

    # 2. Public APIs
    if CONFIG.get("enable_public_apis", True):
        log.info("📡 Fetching from public APIs...")
        public_jobs = fetch_public_apis()
        all_jobs.extend(public_jobs)
        # Count sources
        for job in public_jobs:
            source_counts[job.get("source", "unknown")] += 1

    # 3. JobSpy
    if CONFIG.get("enable_jobspy", True):
        log.info("📡 Fetching from JobSpy...")
        jobspy_jobs = fetch_jobspy()
        all_jobs.extend(jobspy_jobs)
        source_counts["jobspy"] += len(jobspy_jobs)

    # 4. Custom boards
    for board in CONFIG.get("custom_boards", []):
        log.info(f"📡 Fetching custom board '{board.get('name')}'...")
        board_jobs = fetch_custom_board(board)
        all_jobs.extend(board_jobs)
        source_counts[f"custom_{board.get('name')}"] += len(board_jobs)

    log.info(f"\n📊 Total fetched: {len(all_jobs)}")
    log.info(f"   Sources: {dict(source_counts)}")

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

    # Update performance tracking
    perf = load_performance()
    for source, count in source_counts.items():
        perf.setdefault(source, {"total": 0, "successful": 0})
        perf[source]["total"] += count
        perf[source]["successful"] += count  # all fetched are considered successful (errors are separate)
    # Store errors separately? We'll just log.
    save_performance(perf)

    log.info(f"   ✅ {len(filtered)} jobs matched (after filter & ghost)")
    if filtered:
        send_telegram(filtered)
    else:
        log.info("ℹ️ No new remote jobs found")
        send_telegram([])

    log.info("✅ Job hunt complete!")
    # Log quick stats
    log.info(f"   Jobs fetched: {len(all_jobs)}")
    log.info(f"   Jobs matched: {len(filtered)}")
    log.info(f"   Ghosts filtered: {len(all_jobs) - len(filtered)}")

if __name__ == "__main__":
    main()
