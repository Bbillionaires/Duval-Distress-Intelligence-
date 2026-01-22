import re
from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

m = re.search(r'(?ms)^\s*if __name__\s*==\s*["\']__main__["\']\s*:\s*\n(?:.*\n)*?(?=^\S|\Z)', t)
if not m:
    print("NO __main__ BLOCK FOUND (SKIP)")
    raise SystemExit(0)

main_block = m.group(0).rstrip() + "\n\n"
t2 = t[:m.start()] + t[m.end():]

# append __main__ at very end
t2 = t2.rstrip() + "\n\n" + main_block

p.write_text(t2, encoding="utf-8")
print("MOVED __main__ TO BOTTOM")
