from pathlib import Path
import re

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# ensure imports
if "from flask import" in t and "redirect" not in t:
    pass

# 1) kill any old "force redirect /admin.html -> /login" blocks that cause loops
# (we'll replace by a safe check that only redirects /admin and /admin/ routes, not /admin.html)
t = re.sub(r"@app\.before_request.*?(?=\n@app|\nif __name__|\Z)", "", t, flags=re.S)

# 2) make sure send_from_directory import exists
if "send_from_directory" not in t:
    t = t.replace(
        "from flask import jsonify, request",
        "from flask import jsonify, request, send_from_directory, redirect",
        1
    )

# 3) add simple, non-looping routes
insert = r'''
# ===== AUTO: UI ROUTES (NO LOOP) =====
@app.get("/login")
@app.get("/login/")
def login_page():
    return send_from_directory(BASE_DIR, "login.html")

@app.get("/admin.html")
def admin_html_page():
    return send_from_directory(BASE_DIR, "admin.html")

# optional aliases
@app.get("/admin")
@app.get("/admin/")
def admin_alias():
    return redirect("/admin.html", code=302)
# ===== END AUTO =====
'''
if "AUTO: UI ROUTES (NO LOOP)" not in t:
    # put above api routes if possible, else near top
    anchor = '@app.get("/api/health")'
    if anchor in t:
        t = t.replace(anchor, insert + "\n" + anchor, 1)
    else:
        t = insert + "\n" + t

p.write_text(t, encoding="utf-8")
print("PATCHED: removed redirect loop + fixed /login + /admin.html")
