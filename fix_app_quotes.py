from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

bad = r'return (send_from_directory(BASE_DIR, \"admin.html\") if _require_admin() else redirect(\"/login\"))'
good = 'return (send_from_directory(BASE_DIR, "admin.html") if _require_admin() else redirect("/login"))'

if bad in t:
    t = t.replace(bad, good)

t = t.replace('\\"admin.html\\"', '"admin.html"').replace('\\"/login\\"', '"/login"')

p.write_text(t, encoding="utf-8")
print("FIX_APP_QUOTES_OK")
