from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# ensure send_from_directory import exists
if "send_from_directory" not in t:
    if "from flask import jsonify, request" in t:
        t = t.replace(
            "from flask import jsonify, request",
            "from flask import jsonify, request, send_from_directory"
        )
    elif "from flask import" in t:
        # fallback: add import line near other imports
        t = t.replace("from flask import", "from flask import send_from_directory,")
        
route = (
    '@app.get("/")\n'
    "def index_page():\n"
    '    return send_from_directory(BASE_DIR, "index.html")\n\n'
)

marker = '@app.get("/api/delinquent")'
if "def index_page" not in t and marker in t:
    t = t.replace(marker, route + marker, 1)

p.write_text(t, encoding="utf-8")
print("PATCHED / ROUTE")
