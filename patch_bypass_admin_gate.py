from pathlib import Path
import re

# ===== login.html: force bypass (auto-forward to admin) =====
lp = Path("login.html")
lt = lp.read_text(encoding="utf-8", errors="ignore")

# add a hard redirect right after <body>
if "/* HARD_BYPASS_LOGIN */" not in lt:
    lt = lt.replace(
        "<body>",
        "<body>\n<script>/* HARD_BYPASS_LOGIN */ location.replace('/admin.html');</script>\n",
        1
    )

# if a Skip button exists, force it to go to admin.html
lt = re.sub(r'onclick\s*=\s*"[^"]*skip[^"]*"', 'onclick="location.href=\'/admin.html\'"', lt, flags=re.I)
lt = re.sub(r"onclick\s*=\s*'[^']*skip[^']*'", "onclick='location.href=\"/admin.html\"'", lt, flags=re.I)

lp.write_text(lt, encoding="utf-8")
print("PATCHED login.html")

# ===== admin.html: remove/disable password gate overlay if present =====
ap = Path("admin.html")
at = ap.read_text(encoding="utf-8", errors="ignore")

# remove common overlay containers by id/class (best-effort)
at2 = at
at2 = re.sub(r"<div[^>]+id\s*=\s*['\"]password[^'\"]*['\"][\s\S]*?</div>\s*</div>", "", at2, flags=re.I)
at2 = re.sub(r"<div[^>]+id\s*=\s*['\"]admin[^'\"]*password[^'\"]*['\"][\s\S]*?</div>", "", at2, flags=re.I)
at2 = re.sub(r"<div[^>]+class\s*=\s*['\"][^'\"]*overlay[^'\"]*['\"][\s\S]*?</div>", "", at2, flags=re.I)

# disable any JS redirect to /login
at2 = re.sub(r"location\.href\s*=\s*['\"]/login[^'\"]*['\"]\s*;?", "// disabled login redirect", at2, flags=re.I)
at2 = re.sub(r"window\.location\s*=\s*['\"]/login[^'\"]*['\"]\s*;?", "// disabled login redirect", at2, flags=re.I)
at2 = re.sub(r"location\.replace\s*\(\s*['\"]/login[^'\"]*['\"]\s*\)\s*;?", "// disabled login redirect", at2, flags=re.I)

# if admin.html checks for "authed" flag, force it true
if "/* FORCE_AUTH_TRUE */" not in at2:
    at2 = at2.replace(
        "</script>",
        "\n<script>/* FORCE_AUTH_TRUE */ try{localStorage.setItem('authed','true');localStorage.setItem('is_admin','true');}catch(e){} </script>\n</script>",
        1
    )

ap.write_text(at2, encoding="utf-8")
print("PATCHED admin.html")
