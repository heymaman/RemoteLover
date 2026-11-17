"""Draft personalised cold emails for the best jobs. NOTHING is ever sent automatically.

    python outreach.py --niche customer_support --min-score 60 --limit 10
    python outreach.py --llm            # polish drafts with Kimi if KIMI_API_KEY is set

Drafts land in outreach/drafts/*.txt plus outreach/index.csv. You review, add the recipient and send
from your own mail client. Jobs already drafted are skipped (tracked in the DB 'outreach' table).
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from config import get_settings  # noqa: E402

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IGNORE_EMAIL = re.compile(r"(noreply|no-reply|example\.com|sentry|\.png|\.jpg)", re.I)


def load_profile(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Missing {path.name}. Copy profile.example.json to profile.json and fill it in.")
    return json.loads(path.read_text(encoding="utf-8"))


def find_contact(content: str) -> str:
    """Only emails the posting itself publishes (e.g. jobs@company.com)."""
    for m in EMAIL_RE.findall(content or ""):
        if not IGNORE_EMAIL.search(m):
            return m
    return ""


def niche_of(skills: list) -> str:
    for s in skills:
        if s.startswith("niche:"):
            return s.split(":", 1)[1]
    return "default"


def build_draft(job: dict, profile: dict, niche: str) -> tuple:
    strengths = profile.get("strengths", {}).get(niche) or profile.get("strengths", {}).get("default", [])
    bullets = "\n".join(f"- {s}" for s in strengths)
    links = "\n".join(profile.get("links", []))
    subject = f"{job['title']} - {profile['name']}"
    body = (
        f"Hi {job['company']} team,\n\n"
        f"I saw your {job['title']} opening and I'd like to be considered. "
        f"I'm a {profile.get('headline', 'remote professional')} based in {profile.get('location', '[LOCATION]')}.\n\n"
        f"What I can bring:\n{bullets}\n\n"
        f"{profile.get('availability', '')}\n\n"
        f"{profile.get('closing_ask', 'Would you be open to a short chat?')}\n\n"
        f"Thanks for your time,\n{profile['name']}\n{profile.get('email', '')}\n{links}\n\n"
        f"Job link: {job['url']}\n"
    )
    return subject, body


def polish_with_kimi(body: str, job: dict) -> str:
    key = get_settings().KIMI_API_KEY
    if not key:
        return body
    try:
        import httpx
        r = httpx.post(
            "https://api.moonshot.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": get_settings().KIMI_MODEL, "temperature": 0.4, "messages": [
                {"role": "system", "content": "Rewrite this cold email to be warm, specific and under 120 words. "
                 "Keep every fact exactly as given; do not invent experience, numbers or claims. Return only the email body."},
                {"role": "user", "content": f"Role: {job['title']} at {job['company']}\n\n{body}"}]},
            timeout=40)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # keep the template draft on any failure
        print(f"  (LLM polish skipped: {e})")
        return body


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche")
    ap.add_argument("--min-score", type=int, default=60)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--profile", default="profile.json")
    ap.add_argument("--llm", action="store_true", help="polish with Kimi (needs KIMI_API_KEY)")
    a = ap.parse_args()

    profile = load_profile(ROOT / a.profile)
    db_path = get_settings().DB_PATH
    if not Path(db_path).exists():
        sys.exit("No database yet. Run: python run.py")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS outreach (
        job_id TEXT PRIMARY KEY, drafted_at TEXT, contact TEXT, status TEXT DEFAULT 'drafted')""")

    rows = conn.execute(
        """SELECT * FROM jobs WHERE score >= ? AND status = 'new'
           AND id NOT IN (SELECT job_id FROM outreach) ORDER BY score DESC""", (a.min_score,)).fetchall()
    out_dir = ROOT / "outreach" / "drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = ROOT / "outreach" / "index.csv"
    new_index = not index_path.exists()
    made = 0
    with open(index_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_index:
            w.writerow(["drafted_at", "score", "company", "title", "contact_email", "job_url", "draft_file"])
        for row in rows:
            job = dict(row)
            skills = json.loads(job["skills"]) if job.get("skills") else []
            niche = niche_of(skills)
            if a.niche and niche != a.niche:
                continue
            subject, body = build_draft(job, profile, niche)
            if a.llm:
                body = polish_with_kimi(body, job)
            contact = find_contact(job.get("content", ""))
            fname = f"{slug(job['company'])}-{slug(job['title'])}.txt"
            (out_dir / fname).write_text(
                f"To: {contact or '[ADD RECIPIENT - check the job page or company site]'}\n"
                f"Subject: {subject}\n\n{body}", encoding="utf-8")
            now = datetime.utcnow().isoformat(timespec="seconds")
            w.writerow([now, job["score"], job["company"], job["title"], contact, job["url"], fname])
            conn.execute("INSERT OR IGNORE INTO outreach (job_id, drafted_at, contact) VALUES (?,?,?)",
                         (job["id"], now, contact))
            made += 1
            if made >= a.limit:
                break
    conn.commit()
    conn.close()
    print(f"Wrote {made} drafts to {out_dir}. Review each one, add the recipient, and send it yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
