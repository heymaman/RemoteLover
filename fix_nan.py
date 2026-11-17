"""One-time patch: clean NaN values from JobSpy rows. Run from the RemoteLover folder: python fix_nan.py"""
p = "scripts/scraper_v4.py"
s = open(p, encoding="utf-8", newline="").read()
marker = "        # Process with Kimi K2"
if "_clean_nan_marker" in s:
    raise SystemExit("already patched")
assert s.count(marker) == 1, f"marker found {s.count(marker)} times"
block = (
    "        # _clean_nan_marker: JobSpy/pandas leave NaN floats in empty cells; clean them up\n"
    "        cleaned = []\n"
    "        for job in jobs_to_process:\n"
    "            for key in ('title', 'company', 'location', 'url', 'source_url', 'content'):\n"
    "                if not isinstance(job.get(key), str):\n"
    "                    job[key] = ''\n"
    "            for key in ('salary_min', 'salary_max'):\n"
    "                v = job.get(key)\n"
    "                if isinstance(v, float) and v != v:\n"
    "                    job[key] = None\n"
    "            if not isinstance(job.get('salary_text'), (str, type(None))):\n"
    "                job['salary_text'] = None\n"
    "            if job['title'] and job['url']:\n"
    "                cleaned.append(job)\n"
    "        jobs_to_process = cleaned\n"
    "        \n"
)
s = s.replace(marker, block + marker)
open(p, "w", encoding="utf-8", newline="").write(s)
print("patched")
