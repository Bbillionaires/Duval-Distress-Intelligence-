from pathlib import Path
import re

p = Path("app_saas.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# 1) Ensure timezone is imported
# Handles either: "from datetime import datetime, timedelta" or already includes timezone
m = re.search(r'(?m)^from\s+datetime\s+import\s+(.+)\s*$', t)
if m:
    imports = [x.strip() for x in m.group(1).split(",")]
    if "timezone" not in imports:
        imports.append("timezone")
        new_line = "from datetime import " + ", ".join(imports)
        t = re.sub(r'(?m)^from\s+datetime\s+import\s+.+\s*$', new_line, t, count=1)
else:
    # If file didn't import from datetime in that style, add a safe import near top
    if "timezone" not in t:
        t = "from datetime import timezone\n" + t

# 2) Make all utcnow() calls timezone-aware
t = t.replace("datetime.utcnow()", "datetime.now(timezone.utc)")

p.write_text(t, encoding="utf-8")
print("PATCH_OK: timezone-aware UTC applied (utcnow -> now(timezone.utc))")
