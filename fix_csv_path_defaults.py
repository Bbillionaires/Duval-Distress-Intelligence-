from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Insert CSV_PATH defaults right before /api/health so it's guaranteed defined
marker = "### CSV DEFAULTS (AUTO) ###"
block = marker + """
import os
try:
    BASE_DIR
except NameError:
    BASE_DIR = Path(__file__).resolve().parent

CSV_PATH = os.getenv("CSV_PATH", str(BASE_DIR / "leads.csv"))
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))
"""

if marker not in t:
    # Find the first definition of the health endpoint and insert above it
    m = re.search(r'(?m)^\s*@app\.route\(\s*["\']\/api\/health["\']\s*\)\s*$', t)
    if not m:
        m = re.search(r'(?m)^\s*@app\.(get|post)\(\s*["\']\/api\/health["\']\s*\)\s*$', t)
    if m:
        t = t[:m.start()] + block + "\n" + t[m.start():]
    else:
        # fallback: put at top
        t = block + "\n" + t

p.write_text(t, encoding="utf-8")
print("FIXED_CSV_PATH_DEFAULTS_OK")
