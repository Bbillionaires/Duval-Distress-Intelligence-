from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

if "send_from_directory" not in t:
    t = t.replace(
        "from flask import jsonify, request",
        "from flask import jsonify, request, send_from_directory",
        1
    )

insert = """
@app.get("/admin")
@app.get("/admin/")
def admin_alias():
    return send_from_directory(BASE_DIR, "admin.html")

@app.get("/login")
@app.get("/login/")
def login_alias():
    return send_from_directory(BASE_DIR, "login.html")
"""

if "def admin_alias" not in t:
    anchor = '@app.get("/api/health")'
    if anchor in t:
        t = t.replace(anchor, insert + "\n" + anchor, 1)
    else:
        # fallback: put near top after BASE_DIR definition if anchor missing
        t = t.replace("BASE_DIR", "BASE_DIR", 1) + "\n" + insert

p.write_text(t, encoding="utf-8")
print("PATCHED: /admin + /login aliases")
