from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# find first route decorator usage
m_route = re.search(r'(?m)^\s*@app\.(route|get|post|put|delete|patch)\b', t)
first_route_pos = m_route.start() if m_route else None

# find first app creation
m_app = re.search(r'(?m)^\s*app\s*=\s*Flask\(__name__\)', t)
app_pos = m_app.start() if m_app else None

# ensure Flask import exists
has_flask_import = bool(re.search(r'(?m)^\s*from\s+flask\s+import\b.*\bFlask\b', t)) or bool(re.search(r'(?m)^\s*import\s+flask\b', t))

# if routes exist before app is created, insert app init before first route
needs_insert = (first_route_pos is not None) and (app_pos is None or app_pos > first_route_pos)

if needs_insert:
    # add/patch flask import line
    if not has_flask_import:
        # put Flask import near top after other imports
        t = re.sub(r'(?m)^(import\s+[^\n]+\n)+',
                   lambda m: m.group(0) + "from flask import Flask\n",
                   t, count=1) or ("from flask import Flask\n\n" + t)
    else:
        # if there's "from flask import ..." without Flask, add Flask into it
        def add_flask(m):
            line = m.group(0)
            if "Flask" in line:
                return line
            return line.rstrip()[:-1] + ", Flask\n" if line.rstrip().endswith(")") else line.rstrip() + ", Flask\n"
        t = re.sub(r'(?m)^\s*from\s+flask\s+import\s+[^\n]+$', add_flask, t, count=1)

    insert = "app = Flask(__name__)\n\n"
    t = t[:first_route_pos] + insert + t[first_route_pos:]

# comment out any later duplicate app = Flask(__name__) lines (keep first)
lines = t.splitlines(True)
seen = 0
out = []
for line in lines:
    if re.match(r'^\s*app\s*=\s*Flask\(__name__\)\s*$', line):
        seen += 1
        if seen > 1:
            out.append("# " + line)
            continue
    out.append(line)

t2 = "".join(out)
p.write_text(t2, encoding="utf-8")
print("FIX_APP_DEFINED_OK")
