"""Config-driven niches. Add a niche by adding a key to niches.json - no code changes."""
import json
import re
from pathlib import Path
from typing import Dict, List

NICHES_FILE = Path(__file__).parent / "niches.json"

# Phrases that usually mean "not open to candidates in Nigeria / most countries".
RESTRICTED = re.compile(
    r"\b(us|u\.s\.|usa|united states|uk|eu|canada)[- ](only|based|residents?)\b|"
    r"must (be located|reside|live) in (the )?(us|united states|uk|canada|eu)|"
    r"(us|u\.s\.) work authori[sz]ation|authori[sz]ed to work in the (us|united states)",
    re.I,
)

# Titles that are senior / technical / managerial - not entry-level remote support work.
SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head of|director|vp|manager|architect|engineer|"
    r"developer|scientist|counsel|executive)\b", re.I)
# Titles locked to a region, e.g. "Customer Success Engineer, Japan" or "- East".
REGION_LOCKED = re.compile(
    r"(,|\s-\s)\s*(australia|france|japan|india|singapore|germany|uk|us|usa|canada|emea|latam|apac|"
    r"ceur|neur|east|west|central|northeast|southeast|pubsec|federal)\b", re.I)
ENTRY = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|associate|assistant|trainee|intern|agent|specialist|"
    r"representative|coordinator|moderator|annotator)\b", re.I)


def load_niches(path: Path = NICHES_FILE) -> Dict[str, List[str]]:
    return {k: [x.lower() for x in v] for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def match_niches(job, niches: Dict[str, List[str]]) -> List[str]:
    text_title = job.title.lower()
    text_all = f"{text_title} {job.content[:1500].lower()}"
    hits = []
    for name, kws in niches.items():
        if any(k in text_title for k in kws) or sum(k in text_all for k in kws) >= 2:
            hits.append(name)
    return hits


def apply_niches(jobs, niches=None, boost: int = 20, penalty: int = 35):
    """Tag jobs with niches (as 'niche:<name>' in skills), boost matches, penalise geo-locked."""
    niches = niches or load_niches()
    for job in jobs:
        hits = match_niches(job, niches)
        for h in hits:
            tag = f"niche:{h}"
            if tag not in job.skills:
                job.skills.append(tag)
        if SENIOR.search(job.title):
            job.score = max(0, job.score - 45)
        if REGION_LOCKED.search(job.title):
            job.score = max(0, job.score - 25)
            job.remote_issue = job.remote_issue or "title is tied to a specific region"
        if ENTRY.search(job.title):
            job.score = min(100, job.score + 15)
        if hits:
            job.score = min(100, job.score + boost)
        if RESTRICTED.search(f"{job.location} {job.content[:3000]}"):
            job.score = max(0, job.score - penalty)
            job.remote_issue = job.remote_issue or "may be restricted to US/UK/EU residents"
    return jobs
