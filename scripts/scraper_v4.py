import httpx
import asyncio
import random
import hashlib
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from models import Job, JobType, JobStatus, EnrichedData, RemoteLevel, ScrapeResult
from config import get_settings

settings = get_settings()

# ─── USER AGENTS ──────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]

def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def normalize_date(date_str) -> datetime:
    """Normalize various date formats to datetime."""
    if not date_str:
        return datetime.utcnow()
    try:
        if isinstance(date_str, (int, float)):
            return datetime.utcfromtimestamp(date_str)
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d, %Y",
            "%B %d, %Y", "%d %b %Y", "%d %B %Y"
        ]:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
    except Exception:
        return datetime.utcnow()

def normalize_salary(salary_data) -> Dict[str, Any]:
    """Normalize salary data from various formats."""
    result = {"min": None, "max": None, "text": ""}
    if isinstance(salary_data, dict):
        result["min"] = salary_data.get("min")
        result["max"] = salary_data.get("max")
        result["text"] = salary_data.get("text", "")
    elif isinstance(salary_data, str) and salary_data:
        result["text"] = salary_data
        # Try to extract numbers
        cleaned = re.sub(r'[^0-9,\-]', ' ', salary_data).strip()
        nums = re.findall(r'[\d,]+', cleaned)
        cleaned_nums = [int(n.replace(',', '')) for n in nums if n]
        if len(cleaned_nums) >= 2:
            # Find the pair of numbers that makes sense as a range
            if max(cleaned_nums) - min(cleaned_nums) < 200000:
                result["min"] = min(cleaned_nums)
                result["max"] = max(cleaned_nums)
            else:
                result["min"] = cleaned_nums[0]
                result["max"] = cleaned_nums[-1]
        elif len(cleaned_nums) == 1:
            result["min"] = cleaned_nums[0]
    return result

def compute_dedup_hash(title: str, company: str, location: str) -> str:
    """Compute a hash for deduplication."""
    normalized = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()[:16]

# ─── SCORING ──────────────────────────────────────────────────────────────────

GLOBAL_FRIENDLY = [
    "gitlab", "stripe", "figma", "notion", "linear", "supabase", "airbnb", "vercel", "railway",
    "anthropic", "deepmind", "shopify", "discord", "spotify", "dropbox", "datadog", "elastic",
    "mongodb", "scale ai", "brex", "coursera", "amplitude", "buffer", "doist", "automattic",
    "toptal", "gitcoin", "consensys", "protocol labs", "status", "sourcegraph", "sentry",
    "posthog", "cal.com", "raycast", "excalidraw", "appwrite", "pocketbase", "railway",
    "fly.io", "render", "netlify", "vercel", "cloudflare", "hugging face"
]

GEO_RESTRICTED = [
    "microsoft", "amazon", "google", "apple", "meta", "netflix", "jane street", "citadel",
    "jump trading", "robinhood", "databricks", "roblox", "uber", "lyft", "doordash",
    "palantir", "anduril", "lockheed", "boeing", "raytheon", "northrop", "general dynamics",
    "capital one", "jpmorgan", "goldman sachs", "morgan stanley"
]

EASY_KEYWORDS = [
    "data entry", "virtual assistant", "customer support", "customer success", "support specialist",
    "operations associate", "onboarding", "implementation", "community support", "community manager",
    "administrative assistant", "project coordinator", "trust and safety", "entry level", "junior",
    "trainee", "internship", "task", "microtask", "gig", "freelance", "transcription",
    "annotation", "labeling", "moderation", "content moderator", "user testing", "qa tester",
    "tech support", "help desk", "it support", "administrative", "operations"
]

DIRECT_APPLY_DOMAINS = ["greenhouse.io", "lever.co", "workable.com", "ashbyhq.com", "breezy.hr", "applytojob.com"]

def calculate_score(job: Dict[str, Any]) -> int:
    """Calculate a heuristic score for a job."""
    score = 0
    loc = job.get("location", "").lower()
    desc = job.get("content", "").lower()
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
    url = job.get("url", "").lower()

    # Remote location bonus
    if "anywhere" in loc or "worldwide" in loc or "global" in loc:
        score += 25
    elif "fully remote" in desc or "100% remote" in desc or "remote-first" in desc:
        score += 20
    elif "remote" in loc:
        score += 15
    elif "remote" in desc:
        score += 10

    # Recency bonus
    posted = job.get("posted_at")
    if posted and isinstance(posted, datetime):
        days = (datetime.utcnow() - posted).days
        if days <= 1:
            score += 15
        elif days <= 3:
            score += 10
        elif days <= 7:
            score += 8
        elif days <= 14:
            score += 5
        elif days <= 30:
            score += 2

    # Direct apply bonus
    if any(d in url for d in DIRECT_APPLY_DOMAINS):
        score += 12

    # Easy keywords
    if any(kw in title or kw in desc for kw in EASY_KEYWORDS):
        score += 18

    # Global-friendly company bonus
    if any(gc in company for gc in GLOBAL_FRIENDLY):
        score += 20

    # Geo-restricted penalty
    if any(gr in company for gr in GEO_RESTRICTED):
        score -= 40

    # Salary mentioned
    if job.get("salary_min") or job.get("salary_text"):
        score += 5

    # Description length (fuller descriptions are usually better)
    if job.get("content") and len(job.get("content")) > 500:
        score += 5
    elif job.get("content") and len(job.get("content")) > 200:
        score += 2

    return max(0, min(100, score))

# ─── KIMI K2 CLIENT ──────────────────────────────────────────────────────────

@dataclass
class KimiK2Client:
    """Async client for Kimi K2 API with intelligent job analysis."""
    
    api_key: str
    base_url: str = "https://api.moonshot.ai/v1"
    model: str = "kimi-k2-instruct"
    temperature: float = 0.6
    max_tokens: int = 800
    max_concurrent: int = 5
    _semaphore: asyncio.Semaphore = field(default=None, init=False)
    _cache: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._cache = {}
    
    def _get_cache_key(self, prompt_hash: str, job_id: str) -> str:
        return f"{job_id}_{prompt_hash}"
    
    async def _call_api(self, messages: List[Dict], temperature: float = None, 
                        use_cache: bool = True, cache_key: str = None) -> Optional[str]:
        """Make async API call to Kimi K2 with caching."""
        if use_cache and cache_key and cache_key in self._cache:
            return self._cache[cache_key]
        
        async with self._semaphore:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature or self.temperature,
                "max_tokens": self.max_tokens
            }
            
            async with httpx.AsyncClient(timeout=45.0) as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload
                    )
                    if response.status_code == 200:
                        data = response.json()
                        result = data["choices"][0]["message"]["content"]
                        if use_cache and cache_key:
                            self._cache[cache_key] = result
                        return result
                    elif response.status_code == 429:
                        # Rate limit - wait and retry once
                        await asyncio.sleep(2)
                        return await self._call_api(messages, temperature, use_cache, cache_key)
                    else:
                        return None
                except Exception as e:
                    return None
    
    async def score_job(self, job: Dict, profile_text: str = "") -> int:
        """Score a job intelligently using Kimi K2."""
        if not self.api_key:
            return calculate_score(job)
        
        cache_key = self._get_cache_key("score", job.get("id", ""))
        
        prompt = f"""Score this remote job from 0-100 based on these criteria:

1. True remote flexibility (global hiring, timezone-friendly)
2. Company reputation and culture (remote-first vs remote-tolerant)
3. Job quality (salary, benefits, growth potential)
4. Match with candidate profile

Job: {job.get('title', '')} at {job.get('company', '')}
Location: {job.get('location', '')}
Description: {job.get('content', '')[:1500]}
{job.get('salary_text', '')}

Candidate Profile: {profile_text[:500]}

Return ONLY the score (integer 0-100). No explanation. Consider that:
- 80-100: Excellent remote opportunity
- 60-79: Good remote opportunity
- 40-59: Average remote opportunity
- 20-39: Poor remote opportunity
- 0-19: Not truly remote or low quality"""

        try:
            response = await self._call_api([
                {"role": "user", "content": prompt}
            ], temperature=0.2, use_cache=True, cache_key=cache_key)
            if response:
                numbers = re.findall(r'\d+', response)
                if numbers:
                    score = int(numbers[0])
                    return max(0, min(100, score))
        except Exception:
            pass
        return calculate_score(job)
    
    async def extract_structured_info(self, job: Dict) -> EnrichedData:
        """Extract structured information using Kimi K2."""
        if not self.api_key:
            return EnrichedData()
        
        cache_key = self._get_cache_key("enrich", job.get("id", ""))
        
        prompt = f"""Extract structured information from this job description.
Return ONLY JSON with these fields:
- salary_range: estimated salary range (e.g., "$80k-$120k" or null)
- required_skills: list of top 5 required technical skills
- experience_years: minimum years of experience (number or null)
- visa_sponsorship: true/false (if mentioned)
- benefits: list of top 3 benefits mentioned
- remote_level: one of ["global", "country-specific", "timezone-specific", "office-first"]
- company_culture: brief description of company culture (2-3 words)
- growth_potential: number 0-100 indicating career growth potential

Job: {job.get('title', '')} at {job.get('company', '')}
Description: {job.get('content', '')[:2000]}"""

        try:
            response = await self._call_api([
                {"role": "user", "content": prompt}
            ], temperature=0.1, use_cache=True, cache_key=cache_key)
            if response:
                # Extract JSON
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                    return EnrichedData(
                        salary_range=data.get("salary_range"),
                        required_skills=data.get("required_skills", []),
                        experience_years=data.get("experience_years"),
                        visa_sponsorship=data.get("visa_sponsorship", False),
                        benefits=data.get("benefits", []),
                        remote_level=RemoteLevel(data.get("remote_level", "unknown")),
                        company_culture=data.get("company_culture"),
                        growth_potential=data.get("growth_potential", 50)
                    )
        except Exception:
            pass
        return EnrichedData()
    
    async def detect_remote_authenticity(self, job: Dict) -> Tuple[bool, str]:
        """Determine if a job is genuinely remote-friendly."""
        if not self.api_key:
            is_remote = any(word in job.get('location', '').lower() 
                          for word in ['remote', 'anywhere', 'global', 'worldwide'])
            return is_remote, "heuristic"
        
        cache_key = self._get_cache_key("remote", job.get("id", ""))
        
        prompt = f"""Analyze this job listing and determine if it's truly remote-friendly.
Return JSON: {{"is_remote": true/false, "reason": "brief explanation"}}

Key indicators:
- Global hiring vs country-specific
- Timezone requirements
- Remote-first culture evidence
- Work-from-anywhere policy
- Visa requirements
- Must be in specific country/city

Job: {job.get('title', '')} at {job.get('company', '')}
Location: {job.get('location', '')}
Description: {job.get('content', '')[:1500]}"""

        try:
            response = await self._call_api([
                {"role": "user", "content": prompt}
            ], temperature=0.1, use_cache=True, cache_key=cache_key)
            if response:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                    return data.get("is_remote", False), data.get("reason", "unknown")
        except Exception:
            pass
        
        return any(word in job.get('location', '').lower() 
                  for word in ['remote', 'anywhere']), "heuristic"
    
    async def generate_cover_letter(self, job: Dict, profile: str) -> str:
        """Generate a personalized cover letter."""
        if not self.api_key:
            return ""
        
        cache_key = self._get_cache_key("cover", job.get("id", ""))
        
        prompt = f"""Write a concise, professional cover letter for this role.
Keep it to 3 paragraphs. Be enthusiastic but specific.
Use the candidate's profile to highlight relevant experience.

Role: {job.get('title', '')} at {job.get('company', '')}
Description: {job.get('content', '')[:1200]}

Candidate Profile: {profile[:500]}

Format the letter with proper salutation and closing."""

        try:
            response = await self._call_api([
                {"role": "user", "content": prompt}
            ], temperature=0.7, use_cache=False, cache_key=cache_key)
            return response or ""
        except Exception:
            return ""
    
    async def analyze_company(self, company_name: str) -> Dict:
        """Analyze a company's remote-friendliness."""
        if not self.api_key:
            return {}
        
        cache_key = self._get_cache_key("company", company_name)
        
        prompt = f"""Analyze this company's remote work culture.
Return JSON with: 
- remote_friendly: 0-100 score
- global_hiring: true/false
- culture: brief description
- pros: list of pros
- cons: list of cons

Company: {company_name}"""

        try:
            response = await self._call_api([
                {"role": "user", "content": prompt}
            ], temperature=0.2, use_cache=True, cache_key=cache_key)
            if response:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
        except Exception:
            pass
        return {}
    
    async def enrich_job(self, job: Dict, profile_text: str = "") -> Dict:
        """Full enrichment pipeline for a single job."""
        if not self.api_key:
            job["score"] = calculate_score(job)
            return job
        
        # Run in parallel for efficiency
        score_task = self.score_job(job, profile_text)
        info_task = self.extract_structured_info(job)
        remote_task = self.detect_remote_authenticity(job)
        
        score, info, remote_result = await asyncio.gather(
            score_task, info_task, remote_task, return_exceptions=True
        )
        
        job["score"] = score if isinstance(score, int) else calculate_score(job)
        job["kimi_score"] = job["score"]
        
        # Apply authenticity penalty
        if isinstance(remote_result, tuple) and not remote_result[0]:
            job["score"] = max(0, job["score"] - 30)
            job["remote_issue"] = remote_result[1] if len(remote_result) > 1 else "not truly remote"
        
        # Add enriched data
        if isinstance(info, EnrichedData):
            job["enriched"] = info
            if info.salary_range:
                job["salary_text"] = info.salary_range
            if info.required_skills:
                job["skills"] = info.required_skills
            if info.remote_level:
                job["remote_level"] = info.remote_level.value
        
        return job

# ─── SCRAPER ─────────────────────────────────────────────────────────────────

class Scraper:
    """Base scraper with async fetchers."""
    
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.settings = get_settings()
        self._thread_pool = ThreadPoolExecutor(max_workers=2)
        self.kimi: Optional[KimiK2Client] = None
        
        # Initialize Kimi K2 if API key is available
        if self.settings.KIMI_API_KEY and self.settings.KIMI_ENABLED:
            self.kimi = KimiK2Client(
                api_key=self.settings.KIMI_API_KEY,
                model=self.settings.KIMI_MODEL,
                temperature=self.settings.KIMI_TEMPERATURE,
                max_tokens=self.settings.KIMI_MAX_TOKENS,
                max_concurrent=self.settings.KIMI_MAX_CONCURRENT
            )
    
    async def fetch_with_retry(self, url: str, headers: Optional[Dict] = None,
                               timeout: Optional[int] = None, is_json: bool = True) -> Optional[Any]:
        """Fetch URL with retry logic."""
        headers = headers or random_headers()
        timeout = timeout or self.settings.REQUEST_TIMEOUT
        
        for attempt in range(self.settings.MAX_RETRIES):
            try:
                resp = await self.client.get(url, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    if is_json and "application/json" in resp.headers.get("Content-Type", ""):
                        return resp.json()
                    return resp.text
                elif resp.status_code == 429:
                    wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code == 403 and "Cloudflare" in resp.text:
                    # Cloudflare protection - wait and retry
                    await asyncio.sleep(random.uniform(3, 5))
                    continue
                else:
                    return None
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == self.settings.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep((2 ** attempt) * 0.5 + random.uniform(0, 0.5))
            except Exception as e:
                if attempt == self.settings.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep((2 ** attempt) * 0.5 + random.uniform(0, 0.5))
        return None
    
    async def process_job_batch(self, jobs: List[Dict], profile_text: str = "") -> List[Job]:
        """Process a batch of jobs with Kimi K2 enrichment."""
        if not self.kimi:
            # Fallback to simple scoring
            result = []
            for job in jobs:
                job["score"] = calculate_score(job)
                job["dedup_hash"] = compute_dedup_hash(
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", "")
                )
                if "status" not in job:
                    job["status"] = JobStatus.NEW.value
                if "type" not in job:
                    job["type"] = JobType.JOB.value
                result.append(Job(**job))
            return result
        
        # Process jobs in parallel with concurrency control
        tasks = []
        for job in jobs:
            job_copy = job.copy()
            tasks.append(self.kimi.enrich_job(job_copy, profile_text))
        
        enriched_jobs = await asyncio.gather(*tasks, return_exceptions=True)
        
        result = []
        for item in enriched_jobs:
            if isinstance(item, dict):
                # Add dedup hash
                item["dedup_hash"] = compute_dedup_hash(
                    item.get("title", ""),
                    item.get("company", ""),
                    item.get("location", "")
                )
                if "status" not in item:
                    item["status"] = "new"
                if "type" not in item:
                    item["type"] = "job"
                if "posted_at" in item and isinstance(item["posted_at"], datetime):
                    pass
                result.append(Job(**item))
            elif isinstance(item, Exception):
                # Log error but continue
                pass
        
        return result
    
    # ─── FETCHERS ────────────────────────────────────────────────────────────
    
    async def fetch_remoteok(self) -> List[Dict]:
        """Fetch jobs from RemoteOK."""
        data = await self.fetch_with_retry("https://remoteok.com/api")
        if not data or not isinstance(data, list):
            return []
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
                    "type": JobType.JOB.value,
                    "content": item.get("description", "")
                })
        return jobs
    
    async def fetch_remotive(self) -> List[Dict]:
        """Fetch jobs from Remotive."""
        data = await self.fetch_with_retry("https://remotive.com/api/remote-jobs")
        if not data or not isinstance(data, dict):
            return []
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
                "type": JobType.JOB.value,
                "content": job.get("description", "")
            })
        return jobs
    
    async def fetch_himalayas(self) -> List[Dict]:
        """Fetch jobs from Himalayas."""
        data = await self.fetch_with_retry("https://himalayas.app/jobs/api?limit=50")
        if not data or not isinstance(data, dict):
            return []
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
                "type": JobType.JOB.value,
                "content": job.get("description", "")
            })
        return jobs
    
    async def fetch_weworkremotely(self) -> List[Dict]:
        """Fetch jobs from WeWorkRemotely RSS."""
        text = await self.fetch_with_retry("https://weworkremotely.com/remote-jobs.rss", is_json=False)
        if not text:
            return []
        try:
            root = ET.fromstring(text.encode())
        except ET.ParseError:
            return []
        jobs = []
        for item in root.findall(".//item"):
            title_elem = item.find("title")
            link_elem = item.find("link")
            pub_elem = item.find("pubDate")
            desc_elem = item.find("description")
            if title_elem is None or link_elem is None:
                continue
            title = title_elem.text or ""
            company, role = ("", title) if ": " not in title else title.split(": ", 1)
            jobs.append({
                "id": f"wwr_{hashlib.md5(link_elem.text.encode()).hexdigest()[:8]}",
                "title": role,
                "company": company,
                "location": "Remote",
                "url": link_elem.text or "",
                "source": "weworkremotely",
                "source_url": "https://weworkremotely.com/remote-jobs.rss",
                "posted_at": normalize_date(pub_elem.text if pub_elem is not None else ""),
                "type": JobType.JOB.value,
                "content": desc_elem.text if desc_elem is not None else ""
            })
        return jobs
    
    async def fetch_yc_jobs(self) -> List[Dict]:
        """Fetch jobs from Y Combinator."""
        data = await self.fetch_with_retry("https://www.ycombinator.com/companies")
        if not data or not isinstance(data, list):
            return []
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
                        "type": JobType.JOB.value,
                        "content": job.get("description", "")
                    })
        return jobs
    
    async def fetch_greenhouse(self, slug: str) -> List[Dict]:
        """Fetch jobs from a Greenhouse board."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        data = await self.fetch_with_retry(url)
        if not data or not isinstance(data, dict):
            return []
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
                "type": JobType.JOB.value,
                "content": job.get("content", "")
            })
        return jobs
    
    async def fetch_x_tweets(self) -> List[Dict]:
        """Fetch job tweets from X (Twitter)."""
        bearer = self.settings.X_BEARER_TOKEN
        if not bearer:
            return []
        queries = ['"we\'re hiring" remote', '"join our team" remote', '"open position" remote']
        jobs = []
        headers = {"Authorization": f"Bearer {bearer}"}
        for q in queries:
            try:
                data = await self.fetch_with_retry(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers=headers, timeout=15
                )
                if data and isinstance(data, dict):
                    for tweet in data.get("data", []):
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
                            "type": JobType.JOB.value,
                            "content": text
                        })
            except Exception:
                continue
        return jobs
    
    async def fetch_reddit_jobs(self) -> List[Dict]:
        """Fetch job posts from Reddit."""
        subreddits = ["forhire", "remotejobs", "startups"]
        jobs = []
        headers = {"User-Agent": "Mozilla/5.0"}
        for sub in subreddits:
            try:
                data = await self.fetch_with_retry(
                    f"https://www.reddit.com/r/{sub}/search.json?q=hiring+remote&restrict_sr=1&limit=20",
                    headers=headers, timeout=10
                )
                if data and isinstance(data, dict) and "data" in data and "children" in data["data"]:
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
                            "type": JobType.JOB.value,
                            "content": post.get("selftext", "")[:500]
                        })
            except Exception:
                continue
        return jobs
    
    async def fetch_reddit_tasks(self) -> List[Dict]:
        """Fetch task/gig posts from Reddit."""
        subreddits = ["slavelabour", "beermoney", "workonline", "forhire", "freelance"]
        keywords = ["need help", "looking for", "paid", "gig", "task", "microtask", "user testing", "transcription"]
        jobs = []
        headers = {"User-Agent": "Mozilla/5.0"}
        for sub in subreddits:
            query = " OR ".join(keywords)
            try:
                data = await self.fetch_with_retry(
                    f"https://www.reddit.com/r/{sub}/search.json?q={query}&restrict_sr=1&limit=20&sort=new",
                    headers=headers, timeout=10
                )
                if data and isinstance(data, dict) and "data" in data and "children" in data["data"]:
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
                                "type": JobType.TASK.value,
                                "content": post.get("selftext", "")
                            })
            except Exception:
                continue
        return jobs
    
    async def fetch_hn_jobs(self) -> List[Dict]:
        """Fetch job posts from Hacker News."""
        try:
            top = await self.fetch_with_retry("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
            if not top or not isinstance(top, list):
                return []
            jobs = []
            for story_id in top[:30]:
                story = await self.fetch_with_retry(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=10)
                if story and isinstance(story, dict) and "title" in story and "Who is hiring?" in story["title"]:
                    for kid_id in story.get("kids", [])[:30]:
                        comment = await self.fetch_with_retry(f"https://hacker-news.firebaseio.com/v0/item/{kid_id}.json", timeout=10)
                        if comment and isinstance(comment, dict) and "text" in comment:
                            jobs.append({
                                "id": f"hn_{kid_id}",
                                "title": "HN Job",
                                "company": "Hacker News",
                                "location": "Remote",
                                "url": f"https://news.ycombinator.com/item?id={kid_id}",
                                "source": "hn",
                                "source_url": f"https://news.ycombinator.com/item?id={kid_id}",
                                "posted_at": normalize_date(comment.get("time")),
                                "type": JobType.JOB.value,
                                "content": comment.get("text", "")[:500]
                            })
                    break
            return jobs
        except Exception:
            return []
    
    async def fetch_github_issues(self) -> List[Dict]:
        """Fetch job-related GitHub issues."""
        url = "https://api.github.com/search/issues?q=hiring+remote+label:help-wanted&per_page=20"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.settings.GITHUB_TOKEN:
            headers["Authorization"] = f"token {self.settings.GITHUB_TOKEN}"
        try:
            data = await self.fetch_with_retry(url, headers=headers, timeout=self.settings.REQUEST_TIMEOUT)
            if data and isinstance(data, dict) and "items" in data:
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
                        "type": JobType.JOB.value,
                        "content": item.get("body", "")[:500]
                    })
                return jobs
        except Exception:
            return []
        return []
    
    async def fetch_google_jobs(self) -> List[Dict]:
        """Fetch jobs/tasks via Google Search (SerpAPI)."""
        api_key = self.settings.SERPAPI_KEY
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
                resp = await self.client.get(
                    "https://serpapi.com/search",
                    params={"q": q, "api_key": api_key, "num": 10},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for result in data.get("organic_results", []):
                        title = result.get("title", "")
                        snippet = result.get("snippet", "")
                        url = result.get("link", "")
                        platform = "Unknown"
                        platforms = ["Upwork", "Fiverr", "UserTesting", "Rev", "TranscribeMe", 
                                   "Mechanical Turk", "Clickworker"]
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
                            "posted_at": datetime.utcnow(),
                            "type": JobType.TASK.value,
                            "content": snippet
                        })
            except Exception:
                continue
        return jobs
    
    async def fetch_wellfound(self) -> List[Dict]:
        """Fetch jobs from Wellfound (AngelList)."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        try:
            resp = await self.client.get(
                "https://wellfound.com/roles",
                headers=random_headers(),
                timeout=20
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                jobs = []
                # Try different selectors for wellfound
                cards = soup.select("[data-test='job-card']") or soup.select(".role-card")
                for card in cards:
                    title_elem = card.select_one("[data-test='job-title']") or card.select_one(".role-title")
                    company_elem = card.select_one("[data-test='company-name']") or card.select_one(".company-name")
                    link_elem = card.select_one("a")
                    if title_elem and link_elem:
                        jobs.append({
                            "id": f"wf_{hashlib.md5(link_elem.get('href', '').encode()).hexdigest()[:8]}",
                            "title": title_elem.text.strip(),
                            "company": company_elem.text.strip() if company_elem else "Wellfound",
                            "location": "Remote" if "remote" in card.text.lower() else "On-site",
                            "url": link_elem.get("href"),
                            "source": "wellfound",
                            "source_url": "https://wellfound.com/roles",
                            "posted_at": datetime.utcnow(),
                            "type": JobType.JOB.value,
                            "content": ""
                        })
                return jobs
        except Exception:
            return []
        return []
    
    async def fetch_jobspy(self) -> List[Dict]:
        """Fetch jobs using JobSpy library."""
        try:
            from jobspy import scrape_jobs
        except ImportError:
            return []
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(self._thread_pool, lambda: scrape_jobs(
                site_name=["indeed", "linkedin", "glassdoor", "google", "zip_recruiter"],
                search_term="remote",
                location="remote",
                is_remote=True,
                results_wanted=self.settings.MAX_RESULTS_PER_SOURCE,
                hours_old=168,
                proxies=None
            ))
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
                    "type": JobType.JOB.value,
                    "content": row.get("description", "")
                })
            return jobs
        except Exception:
            return []
    
    async def fetch_discovered_sources(self, db) -> List[Dict]:
        """Fetch jobs from discovered sources."""
        import aiosqlite
        conn = await aiosqlite.connect(self.settings.DB_PATH)
        conn.row_factory = aiosqlite.Row
        c = await conn.execute("SELECT id, name, url, type FROM sources WHERE active = 1")
        rows = await c.fetchall()
        await conn.close()
        jobs = []
        for row in rows:
            src = {"id": row[0], "name": row[1], "url": row[2], "type": row[3]}
            try:
                data = await self.fetch_with_retry(src["url"], timeout=self.settings.REQUEST_TIMEOUT)
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
                                "type": JobType.JOB.value,
                                "content": item.get("description", item.get("content", ""))
                            })
                elif src["type"] == "rss" and data:
                    root = ET.fromstring(data.encode())
                    for item in root.findall(".//item"):
                        link = item.find("link")
                        title = item.find("title")
                        pub = item.find("pubDate")
                        desc = item.find("description")
                        if link is not None and title is not None:
                            jobs.append({
                                "id": link.text or "",
                                "title": title.text or "",
                                "company": src["name"],
                                "location": "Remote",
                                "url": link.text or "",
                                "source": f"discovered_{src['name'][:10]}",
                                "source_url": src["url"],
                                "posted_at": normalize_date(pub.text if pub is not None else ""),
                                "type": JobType.JOB.value,
                                "content": desc.text if desc is not None else ""
                            })
            except Exception:
                continue
        return jobs
    
    def get_sources(self) -> List[Tuple[str, Callable, bool]]:
        """Returns list of (name, fetcher, enabled) tuples."""
        sources = [
            ("remoteok", self.fetch_remoteok, True),
            ("remotive", self.fetch_remotive, True),
            ("himalayas", self.fetch_himalayas, True),
            ("weworkremotely", self.fetch_weworkremotely, True),
            ("yc", self.fetch_yc_jobs, True),
            ("wellfound", self.fetch_wellfound, True),
            ("x", self.fetch_x_tweets, bool(self.settings.X_BEARER_TOKEN)),
            ("reddit", self.fetch_reddit_jobs, True),
            ("reddit_tasks", self.fetch_reddit_tasks, True),
            ("hn", self.fetch_hn_jobs, True),
            ("github", self.fetch_github_issues, True),
            ("google_search", self.fetch_google_jobs, bool(self.settings.SERPAPI_KEY)),
            ("jobspy", self.fetch_jobspy, True),
        ]
        # Greenhouse boards
        for slug in ["stripe", "anthropic", "figma", "notion", "linear", 
                    "supabase", "gitlab", "vercel", "railway", "cloudflare"]:
            sources.append((f"greenhouse_{slug}", lambda s=slug: self.fetch_greenhouse(s), True))
        return sources
    
    async def fetch_all(self, profile_text: str = "", enabled_sources: List[str] = None) -> ScrapeResult:
        """Fetch from all sources with Kimi K2 intelligence."""
        sources = self.get_sources()
        all_jobs = []
        source_counts = {}
        
        # Filter sources
        if enabled_sources:
            sources = [s for s in sources if s[0] in enabled_sources]
        
        # Fetch in parallel
        tasks = []
        for name, fetcher, enabled in sources:
            if not enabled:
                continue
            tasks.append((name, fetcher()))
        
        # Execute all fetchers
        results = await asyncio.gather(
            *[task for _, task in tasks],
            return_exceptions=True
        )
        
        # Collect results
        jobs_to_process = []
        errors = []
        for (name, _), result in zip(tasks, results):
            if isinstance(result, list):
                jobs_to_process.extend(result)
                source_counts[name] = len(result)
            elif isinstance(result, Exception):
                source_counts[name] = 0
                errors.append(f"{name}: {str(result)}")
        
        # Process with Kimi K2
        if self.kimi and jobs_to_process:
            processed = await self.process_job_batch(jobs_to_process, profile_text)
        else:
            processed = []
            for job in jobs_to_process:
                job["score"] = calculate_score(job)
                job["dedup_hash"] = compute_dedup_hash(
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", "")
                )
                if "status" not in job:
                    job["status"] = "new"
                if "type" not in job:
                    job["type"] = "job"
                processed.append(Job(**job))
        
        return ScrapeResult(
            jobs=processed,
            total_count=len(processed),
            source_counts=source_counts,
            timestamp=datetime.utcnow(),
            kimi_enriched=bool(self.kimi and jobs_to_process),
            errors=errors
        )
    
    async def get_best_opportunities(self, profile_text: str = "", limit: int = 20) -> List[Job]:
        """Get top opportunities using Kimi K2 scoring."""
        result = await self.fetch_all(profile_text)
        
        if not result.jobs:
            return []
        
        # Sort by score descending
        sorted_jobs = sorted(result.jobs, key=lambda j: j.score, reverse=True)
        return sorted_jobs[:limit]
