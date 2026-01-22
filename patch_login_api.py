from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Ensure imports include session + send_from_directory (without breaking existing imports)
if "from flask import" in t and "session" not in t:
    t = re.sub(
        r"from flask import ([^\n]+)",
        lambda m: "from flask import " + ", ".join(sorted(set([x.strip() for x in (m.group(1) + ", session").split(",")]))),
        t,
        count=1
    )

# Ensure secret key exists (needed for session cookie)
if "secret_key" not in t:
    m = re.search(r"(app\s*=\s*Flask\([^\)]*\)\s*\n)", t)
    if m:
        insert = m.group(1) + "app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')\n"
        t = t.replace(m.group(1), insert, 1)

# Add auth block once
marker = "### AUTH_BLOCK_V1"
if marker not in t:
    auth_block = f"""
{marker}
def _is_admin_logged_in():
    try:
        return bool(session.get("admin_logged_in"))
    except Exception:
        return False

@app.post("/api/login")
def api_login():
    data = {{}}
    if request.is_json:
        data = request.get_json(silent=True) or {{}}
    else:
        data = request.form.to_dict() if request.form else {{}}

    password = (data.get("password") or "").strip()
    admin_pw = os.getenv("ADMIN_PASSWORD", "admin")

    if password and password == admin_pw:
        session["admin_logged_in"] = True
        return jsonify({{"ok": True}})

    return jsonify({{"ok": False, "error": "Invalid password"}}), 401

@app.post("/api/logout")
def api_logout():
    try:
        session.clear()
    except Exception:
        pass
    return jsonify({{"ok": True}})

@app.before_request
def _protect_admin_pages():
    path = request.path or ""
    protected_html = {{"/admin.html", "/admin", "/admin/"}}
    protected_api  = {{"/api/rebuild_delinquent"}}

    if path in protected_html:
        if not _is_admin_logged_in():
            return redirect("/login")

    if path in protected_api:
        if not _is_admin_logged_in():
            return jsonify({{"ok": False, "error": "Not logged in"}}), 401
"""
    anchor = '@app.get("/api/health")'
    if anchor in t:
        t = t.replace(anchor, auth_block + "\n" + anchor, 1)
    else:
        # fallback: append near top
        t = auth_block + "\n" + t

p.write_text(t, encoding="utf-8")
print("PATCHED app.py: added /api/login + /api/logout + protection")
