"""Fixes remoteok/remotive 'utf-8 codec can't decode' by not requesting brotli. Run: python fix_headers.py"""
p = "scripts/scraper_v4.py"
s = open(p, encoding="utf-8", newline="").read()
o = '"gzip, deflate, br"'
if o not in s:
    raise SystemExit("nothing to change (already fixed?)")
open(p, "w", encoding="utf-8", newline="").write(s.replace(o, '"gzip, deflate"'))
print("fixed")
