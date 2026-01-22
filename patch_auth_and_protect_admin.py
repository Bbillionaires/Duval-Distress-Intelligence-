from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Ensure imports include session + send_from_directory + jsonify/request
if "session" not in t:
    t = re.sub(
        r"from flask import ([^\n]+)",
        lambda m: ("from flask import " + ", ".join(sorted(set([x.strip() for x in (m.group(1).split(","))] + ["session","send_from_directory","make_response"]))).replace(",,", ",")),
        t,
        count=1
    )

# Ensure SECRET_KEY + ADMIN_PASSWORD exist
if "app.secret_key" not in t and "SECRET_KEY" not in t:
    insert = "\nimport os\n\n# --- Auth config ---\napp.secret_key = os.getenv('SECRET_KEY','dev-secret-change-me')\nADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD','ChangeMe123!')\n\n"
    # put after app = Flask(...)
    t = re.sub(r"(app\s*=\s*Flask\([^\)]*\)\s*)", r"\1" + insert, t, count=1)

# Add helpers + endpoints if missing
if "def _is_admin():" not in t:
    auth_block = r'''
def _is_admin():
    return bool(session.get("is_admin"))

def _require_admin():
    if not _is_admin():
        return False
    return True

@app.post("/api/login")
def api_login():
    # accepts JSON or form
    data = {}
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    pw = (data.get("password") if isinstance(data, dict) else None) or request.form.get("password") or request.values.get("password") or ""
    pw = str(pw).strip()
    if pw == str(ADMIN_PASSWORD):
        session["is_admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid password"}), 401

@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.get("/api/me")
def api_me():
    return jsonify({"ok": True, "is_admin": _is_admin()})
'''
    # add before if __name__ == "__main__"
    if "__name__ == \"__main__\"" in t:
        t = t.replace('if __name__ == "__main__":', auth_block + "\n\nif __name__ == \"__main__\":", 1)
    else:
        t += "\n\n" + auth_block + "\n"

# Protect admin.html route handler(s): any send_from_directory(..., "admin.html") should require admin
t = re.sub(
    r"(return\s+send_from_directory\(\s*BASE_DIR\s*,\s*\"admin\.html\"\s*\))",
    r"return (send_from_directory(BASE_DIR, \"admin.html\") if _require_admin() else redirect(\"/login\"))",
    t
)

# If there's a /admin alias, also protect it similarly (same replacement above covers it)

# Make /login serve login.html and redirect to admin if already logged in
if '@app.get("/login")' in t and "def login_alias" in t:
    t = re.sub(
        r"@app\.get\(\"/login\"\)\s*@app\.get\(\"/login/\"\)\s*def\s+login_alias\(\):\s*return\s+send_from_directory\(BASE_DIR,\s*\"login\.html\"\)",
        "@app.get(\"/login\")\n@app.get(\"/login/\")\ndef login_alias():\n    return redirect(\"/admin.html\") if _is_admin() else send_from_directory(BASE_DIR, \"login.html\")",
        t
    )

p.write_text(t, encoding="utf-8")
print("PATCHED AUTH + /api/login + protect admin")
