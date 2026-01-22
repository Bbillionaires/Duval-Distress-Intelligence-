from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Add send_from_directory import if missing
if "send_from_directory" not in t:
    t = t.replace("from flask import jsonify, request",
                  "from flask import jsonify, request, send_from_directory")

# Add routes to serve admin.html/login.html if missing
if '"/admin.html"' not in t:
    insert_after = 'DELINQ_CSV = BASE_DIR / "duval_delinquent_leads_big.csv"\n'
    block = (
        '\n'
        '@app.get("/admin.html")\n'
        'def serve_admin_html():\n'
        '    return send_from_directory(str(BASE_DIR), "admin.html")\n'
        '\n'
        '@app.get("/login.html")\n'
        'def serve_login_html():\n'
        '    return send_from_directory(str(BASE_DIR), "login.html")\n'
        '\n'
    )
    if insert_after in t:
        t = t.replace(insert_after, insert_after + block)

p.write_text(t, encoding="utf-8")
print("PATCHED app.py (admin.html/login.html routes)")
