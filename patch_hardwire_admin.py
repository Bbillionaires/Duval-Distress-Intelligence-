from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# 1) Fix missing redirect import if present
t = t.replace(
    "from flask import jsonify, request, send_from_directory",
    "from flask import jsonify, request, send_from_directory, redirect"
)
t = t.replace(
    "from flask import jsonify, request, send_from_directory, url_for",
    "from flask import jsonify, request, send_from_directory, url_for, redirect"
)
t = t.replace(
    "from flask import jsonify, request, redirect, url_for, send_from_directory",
    "from flask import jsonify, request, redirect, url_for, send_from_directory"
)

# 2) HARD-WIRE: serve admin/login/index as static files (no auth)
block = r"""
@app.get("/")
def index_page():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/admin")
@app.get("/admin/")
@app.get("/admin.html")
def admin_page():
    return send_from_directory(BASE_DIR, "admin.html")

@app.get("/login")
@app.get("/login/")
@app.get("/login.html")
def login_page():
    return send_from_directory(BASE_DIR, "login.html")
"""

# Remove any older alias blocks we previously injected (safe if not present)
t = re.sub(r'@app\.get\("/admin"\).*?send_from_directory\(BASE_DIR,\s*"admin\.html"\)\s*', "", t, flags=re.S)
t = re.sub(r'@app\.get\("/login"\).*?send_from_directory\(BASE_DIR,\s*"login\.html"\)\s*', "", t, flags=re.S)

# Remove any old "/" index alias we injected previously
t = re.sub(r'@app\.get\("/"\)\s*def index_page\(\):\s*return send_from_directory\(BASE_DIR,\s*"index\.html"\)\s*', "", t, flags=re.S)

# Insert block right before __main__
anchor = 'if __name__ == "__main__":'
if anchor in t and "def admin_page" not in t:
    t = t.replace(anchor, block + "\n\n" + anchor, 1)

p.write_text(t, encoding="utf-8")
print("PATCHED: hard-wired /, /admin(.html), /login(.html) + redirect import")
