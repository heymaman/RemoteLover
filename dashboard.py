import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from pathlib import Path
from models import Job, JobFilter, JobStatus, SourceHealth, Stats

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self.init_schema()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def init_schema(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT 'Remote',
                url TEXT UNIQUE NOT NULL,
                source TEXT,
                source_url TEXT,
                posted_at TEXT,
                score INTEGER DEFAULT 0,
                ghost_score INTEGER,
                scam_score INTEGER,
                status TEXT DEFAULT 'new',
                notes TEXT DEFAULT '',
                type TEXT DEFAULT 'job',
                salary_min INTEGER,
                salary_max INTEGER,
                salary_text TEXT,
                content TEXT,
                dedup_hash TEXT,
                seen_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
            CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedup_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_seen ON jobs(seen_at);

            CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
                title, company, content,
                content='jobs', content_rowid='rowid'
            );

            CREATE TABLE IF NOT EXISTS jobs_archive (
                id TEXT PRIMARY KEY,
                title TEXT, company TEXT, location TEXT, url TEXT,
                source TEXT, source_url TEXT, posted_at TEXT,
                score INTEGER, ghost_score INTEGER, scam_score INTEGER,
                status TEXT, notes TEXT, type TEXT,
                salary_min INTEGER, salary_max INTEGER, salary_text TEXT,
                content TEXT, seen_at TEXT,
                archived_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS source_health (
                name TEXT PRIMARY KEY,
                url TEXT,
                active INTEGER DEFAULT 1,
                last_success TEXT,
                last_failure TEXT,
                consecutive_failures INTEGER DEFAULT 0,
                total_jobs INTEGER DEFAULT 0,
                avg_score REAL DEFAULT 0,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                ended_at TEXT,
                jobs_found INTEGER,
                jobs_new INTEGER,
                sources_checked INTEGER,
                sources_failed INTEGER
            );
        """)
        await self._conn.commit()

    async def insert_job(self, job: Job) -> bool:
        try:
            await self._conn.execute("""
                INSERT INTO jobs (id, title, company, location, url, source, source_url,
                    posted_at, score, ghost_score, scam_score, status, notes, type,
                    salary_min, salary_max, salary_text, content, dedup_hash, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.title, job.company, job.location, job.url,
                job.source, job.source_url, job.posted_at.isoformat(), job.score,
                job.ghost_score, job.scam_score, job.status.value, job.notes,
                job.type.value, job.salary_min, job.salary_max, job.salary_text,
                job.content, job.dedup_hash, job.seen_at.isoformat()
            ))
            await self._conn.execute("""
                INSERT INTO jobs_fts(rowid, title, company, content)
                VALUES ((SELECT rowid FROM jobs WHERE id = ?), ?, ?, ?)
            """, (job.id, job.title, job.company, job.content))
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_jobs(self, filt: JobFilter) -> List[Job]:
        params = []
        conditions = ["1=1"]

        if filt.source:
            conditions.append("source = ?")
            params.append(filt.source)
        if filt.job_type:
            conditions.append("type = ?")
            params.append(filt.job_type.value)
        if filt.min_score > 0:
            conditions.append("score >= ?")
            params.append(filt.min_score)
        if filt.saved_only:
            conditions.append("status = 'saved'")
        if filt.status:
            conditions.append("status = ?")
            params.append(filt.status.value)

        if filt.search:
            conditions.append("(jobs.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?) OR title LIKE ? OR company LIKE ?)")
            params.extend([filt.search, f"%{filt.search}%", f"%{filt.search}%"])

        order_col = "score" if filt.sort_by == "score" else "posted_at" if filt.sort_by == "posted_at" else "seen_at"
        order_dir = "DESC" if filt.sort_order == "desc" else "ASC"

        query = f"""
            SELECT * FROM jobs 
            WHERE {' AND '.join(conditions)}
            ORDER BY {order_col} {order_dir}
            LIMIT ? OFFSET ?
        """
        params.extend([filt.limit, filt.offset])

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_job(row) for row in rows]

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            return self._row_to_job(row) if row else None

    async def update_job_status(self, job_id: str, status: JobStatus, notes: Optional[str] = None):
        updates = ["status = ?"]
        params = [status.value]
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        params.append(job_id)
        await self._conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
        await self._conn.commit()

    async def archive_old_jobs(self, days: int = 90):
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        await self._conn.execute("""
            INSERT OR IGNORE INTO jobs_archive 
            SELECT *, CURRENT_TIMESTAMP FROM jobs WHERE seen_at < ?
        """, (cutoff,))
        await self._conn.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))
        await self._conn.commit()

    async def get_stats(self) -> Stats:
        async with self._conn.execute("SELECT COUNT(*) FROM jobs") as c:
            total = (await c.fetchone())[0]
        async with self._conn.execute("SELECT COUNT(*) FROM jobs WHERE date(seen_at) = date('now')") as c:
            new_today = (await c.fetchone())[0]
        async with self._conn.execute("SELECT AVG(score) FROM jobs") as c:
            avg = (await c.fetchone())[0] or 0
        async with self._conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'saved'") as c:
            saved = (await c.fetchone())[0]
        async with self._conn.execute("SELECT COUNT(*) FROM source_health WHERE active = 1") as c:
            active_src = (await c.fetchone())[0]
        async with self._conn.execute("SELECT COUNT(*) FROM source_health") as c:
            total_src = (await c.fetchone())[0]
        async with self._conn.execute("""
            SELECT source, COUNT(*) as cnt, AVG(score) as avg_sc 
            FROM jobs GROUP BY source ORDER BY cnt DESC LIMIT 5
        """) as c:
            top = [{"source": r[0], "count": r[1], "avg_score": round(r[2] or 0, 1)} for r in await c.fetchall()]

        return Stats(
            total_jobs=total, new_today=new_today, avg_score=round(avg, 1),
            top_sources=top, saved_count=saved,
            sources_active=active_src, sources_total=total_src
        )

    async def update_source_health(self, name: str, success: bool, jobs_found: int = 0,
                                   avg_score: float = 0, url: Optional[str] = None, error: Optional[str] = None):
        now = datetime.utcnow().isoformat()
        if success:
            await self._conn.execute("""
                INSERT INTO source_health (name, url, active, last_success, consecutive_failures, total_jobs, avg_score)
                VALUES (?, ?, 1, ?, 0, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    url = COALESCE(EXCLUDED.url, source_health.url),
                    active = 1,
                    last_success = EXCLUDED.last_success,
                    consecutive_failures = 0,
                    total_jobs = source_health.total_jobs + EXCLUDED.total_jobs,
                    avg_score = ((source_health.avg_score * source_health.total_jobs) + (? * ?)) / 
                                MAX(source_health.total_jobs + ?, 1),
                    last_error = NULL
            """, (name, url, now, jobs_found, avg_score, avg_score, jobs_found, jobs_found))
        else:
            await self._conn.execute("""
                INSERT INTO source_health (name, url, active, last_failure, consecutive_failures, last_error)
                VALUES (?, ?, 1, ?, 1, ?)
                ON CONFLICT(name) DO UPDATE SET
                    url = COALESCE(EXCLUDED.url, source_health.url),
                    last_failure = EXCLUDED.last_failure,
                    consecutive_failures = source_health.consecutive_failures + 1,
                    active = CASE WHEN source_health.consecutive_failures >= 4 THEN 0 ELSE source_health.active END,
                    last_error = EXCLUDED.last_error
            """, (name, url, now, error))
        await self._conn.commit()

    async def get_source_health(self) -> List[SourceHealth]:
        async with self._conn.execute("SELECT * FROM source_health ORDER BY name") as c:
            rows = await c.fetchall()
            return [SourceHealth(
                name=r["name"], url=r["url"], active=bool(r["active"]),
                last_success=datetime.fromisoformat(r["last_success"]) if r["last_success"] else None,
                last_failure=datetime.fromisoformat(r["last_failure"]) if r["last_failure"] else None,
                consecutive_failures=r["consecutive_failures"],
                total_jobs=r["total_jobs"], avg_score=r["avg_score"],
                last_error=r["last_error"]
            ) for r in rows]

    async def log_scrape(self, jobs_found: int, jobs_new: int, sources_checked: int, sources_failed: int):
        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
            INSERT INTO scrape_log (started_at, ended_at, jobs_found, jobs_new, sources_checked, sources_failed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (now, now, jobs_found, jobs_new, sources_checked, sources_failed))
        await self._conn.commit()

    async def get_recent_logs(self, limit: int = 10) -> List[dict]:
        async with self._conn.execute(
            "SELECT * FROM scrape_log ORDER BY id DESC LIMIT ?", (limit,)
        ) as c:
            rows = await c.fetchall()
            return [dict(r) for r in rows]

    def _row_to_job(self, row: aiosqlite.Row) -> Job:
        data = dict(row)
        data["posted_at"] = datetime.fromisoformat(data["posted_at"])
        data["seen_at"] = datetime.fromisoformat(data["seen_at"])
        data["status"] = JobStatus(data["status"])
        data["type"] = JobType(data["type"])
        return Job(**data)
