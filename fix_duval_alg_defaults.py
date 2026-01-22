from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

marker = "### ALGOLIA DEFAULTS (AUTO) ###"
block = marker + r'''
import os
DUVAL_ALG_APP_ID = os.getenv("DUVAL_ALG_APP_ID", "")
DUVAL_ALG_INDEX = os.getenv("DUVAL_ALG_INDEX", "fl-duval.property_tax")
'''

if marker not in t:
    # insert right after the first "import os" if present, else at top
    if re.search(r'(?m)^\s*import\s+os\s*$', t):
        t = re.sub(r'(?m)^\s*import\s+os\s*$',
                   lambda m: m.group(0) + "\n" + block + "\n",
                   t, count=1)
    else:
        t = "import os\n" + block + "\n" + t

p.write_text(t, encoding="utf-8")
print("FIXED_DUVAL_ALG_DEFAULTS_OK")
