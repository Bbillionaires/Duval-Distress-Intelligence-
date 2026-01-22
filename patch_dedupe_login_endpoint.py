from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Rename duplicate login_page endpoints so Flask stops crashing
matches = list(re.finditer(r'\bdef\s+login_page\s*\(', t))
if len(matches) > 1:
    out = []
    last = 0
    for i, m in enumerate(matches):
        out.append(t[last:m.start()])
        if i == 0:
            out.append("def login_page(")
        else:
            out.append(f"def login_page_{i+1}(")
        last = m.end()
    out.append(t[last:])
    t = "".join(out)

p.write_text(t, encoding="utf-8")
print("FIXED duplicate login_page endpoint")
