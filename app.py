import os
import csv
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

import requests
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS

# ------------------------------------------------------------------------------
# Flask setup
# ------------------------------------------------------------------------------
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

DATA_DIR = Path("data")
LEADS_CSV = DATA_DIR / "leads.csv"

# ------------------------------------------------------------------------------
# Helpers: file & CSV
# ------------------------------------------------------------------------------
def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def write_csv(headers: List[str], rows: List[Dict[str, Any]], path: Path) -> int:
    ensure_data_dir()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})
    return path.stat().st_size

def read_leads() -> List[Dict[str, str]]:
    if not LEADS_CSV.exists():
        return []
    with LEADS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def sanitize_github_raw_url(raw_url: str) -> str:
    """
    Clean common issues:
      - trailing whitespace / newline that becomes %0A
      - accidental /blob/ form (convert to raw)
    """
    if not raw_url:
        return raw_url
    cleaned = raw_url.strip().replace("\r", "").replace("\n", "")
    # If someone pasted a 'github.com/.../blob/branch/file' URL, convert to raw
    if "github.com" in cleaned and "/blob/" in cleaned and "raw.githubusercontent.com" not in cleaned:
        parts = cleaned.split("github.com/", 1)[1]
        owner_repo, tail = parts.split("/blob/", 1)
        branch, file_path = tail.split("/", 1)
        cleaned = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{file_path}"
    return cleaned

# ------------------------------------------------------------------------------
# Resilient refresh (GitHub -> data/leads.csv)
# ------------------------------------------------------------------------------
def fetch_github_csv() -> Dict[str, Any]:
    """
    Downloads CSV from GH_RAW_URL and writes it to data/leads.csv.
    Returns dict with status, bytes, and source url.
    """
    ensure_data_dir()

    raw_url = os.getenv("GH_RAW_URL", "")
    raw_url = sanitize_github_raw_url(raw_url)
    if not raw_url:
        return {"status": "error", "message": "GH_RAW_URL not set"}

    try:
        r = requests.get(raw_url, timeout=30)
        # Helpful for surfacing 404/403 (what you hit earlier)
        r.raise_for_status()
        content = r.content
        with LEADS_CSV.open("wb") as f:
            f.write(content)

        size = LEADS_CSV.stat().st_size
        return {"status": "success", "bytes": size, "source": raw_url}
    except Exception as e:
        log.exception("refresh failed")
        return {"status": "error", "message": str(e)}

# ------------------------------------------------------------------------------
# Server-side Duval scraper (Algolia) + fallback logic
# ------------------------------------------------------------------------------
ALG_APP_ID = "0LWZO52LS2"
ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"  # public search key (read-only)
ALG_URL = "https://0lwzo52ls2-dsn.algolia.net/1/indexes/*/queries"
ALG_AGENT = "Algolia for JavaScript (4.23.3); Browser (lite); instantsearch.js (4.66.1); Vue (3.3.4); Vue InstantSearch (4.15.0); JS Helper (3.17.0)"

DUVAL_HEADERS = ["Owner", "Property Address", "Parcel", "Zip", "Distress", "Amount Due"]

def _algolia_query(query_text: str, hits_per_page=1000, page=0) -> Dict[str, Any]:
    headers = {
        "x-algolia-application-id": ALG_APP_ID,
        "x-algolia-api-key": ALG_API_KEY,
        "x-algolia-agent": ALG_AGENT,
        "content-type": "application/json",
    }
    params = (
        f"hitsPerPage={hits_per_page}"
        f"&page={page}"
        f"&highlightPreTag=__ais-highlight__"
        f"&highlightPostTag=__/ais-highlight__"
        f"&clickAnalytics=true"
        f"&query={requests.utils.quote(query_text or '')}"
    )
    payload = {"requests": [{"indexName": "fl-duval.property_tax", "params": params}]}
    r = requests.post(ALG_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    j = r.json()
    return (j.get("results") or [{}])[0]

def scrape_duval(query_text: str = "") -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    page = 0
    while True:
        res = _algolia_query(query_text, page=page)
        hits = res.get("hits") or []
        if not hits:
            break
        for h in hits:
            rows.append({
                "Owner": h.get("owner") or h.get("owner_name") or "",
                "Property Address": h.get("situs") or h.get("address") or "",
                "Parcel": h.get("parcel") or h.get("parcel_id") or h.get("account") or "",
                "Zip": h.get("zip") or "",
                "Distress": ", ".join(h.get("distressTypes", [])) if isinstance(h.get("distressTypes"), list) else (h.get("distressTypes") or ""),
                "Amount Due": h.get("amount_due") or h.get("amountDue") or h.get("balance") or "",
            })
        nb_pages = res.get("nbPages")
        if nb_pages is None or (page + 1) >= nb_pages:
            break
        page += 1
        if page > 20:  # safety cap
            break
    return rows

# ------------------------------------------------------------------------------
# Routes: static
# ------------------------------------------------------------------------------
@app.get("/")
def serve_index():
    root = Path(".").resolve()
    target = root / "index.html"
    log.info("Serving index. cwd=%s root=%s/index.html exists=%s", os.getcwd(), root, target.exists())
    if target.exists():
        return send_from_directory(root, "index.html")
    return "Not Found", 404

@app.get("/login")
def serve_login():
    root = Path(".").resolve()
    file = root / "login.html"
    if file.exists():
        return send_from_directory(root, "login.html")
    return "Not Found", 404

@app.get("/admin")
def serve_admin():
    root = Path(".").resolve()
    file = root / "admin.html"
    if file.exists():
        return send_from_directory(root, "admin.html")
    return "Not Found", 404

# ------------------------------------------------------------------------------
# Routes: debugging
# ------------------------------------------------------------------------------
@app.get("/debug/file/<path:filepath>")
def debug_file(filepath: str):
    p = Path(filepath)
    try_path = p if p.is_absolute() else Path(".") / filepath
    return jsonify({
        "exists": try_path.exists(),
        "path": str(try_path.resolve()),
        "size": try_path.stat().st_size if try_path.exists() else 0
    })

# ------------------------------------------------------------------------------
# Routes: API - leads (read CSV, filter)
# ------------------------------------------------------------------------------
@app.get("/api/leads")
def api_leads():
    rows = read_leads()

    # Simple filters your UI already sends
    q = (request.args.get("q") or "").strip().lower()
    zip_code = (request.args.get("zip") or "").strip()
    min_amount = request.args.get("min_amount")
    max_amount = request.args.get("max_amount")

    def as_num(x):
        try:
            return float(str(x).replace(",", "").replace("$", ""))
        except Exception:
            return 0.0

    out = []
    for r in rows:
        if q:
            blob = " ".join([r.get(k, "") for k in r.keys()]).lower()
            if q not in blob:
                continue
        if zip_code and zip_code != (r.get("Zip") or ""):
            continue
        amt = as_num(r.get("Amount Due", 0))
        if min_amount and amt < float(min_amount):
            continue
        if max_amount and amt > float(max_amount):
            continue
        out.append(r)

    return jsonify(out)

# ------------------------------------------------------------------------------
# Routes: API - refresh from GitHub with fallback to live scrape
# ------------------------------------------------------------------------------
@app.get("/api/refresh")
def api_refresh():
    # 1) Try GitHub raw CSV
    gh = fetch_github_csv()
    if gh.get("status") == "success":
        size = gh.get("bytes", 0) or 0
        # Headers-only CSVs are ~70–80 bytes in your project
        if size and size > 80:
            return jsonify(gh)

    # 2) Fallback to live scrape if GH is missing/empty
    try:
        app.logger.info("GitHub CSV missing or tiny; running live scrape fallback…")
        rows = scrape_duval(query_text="")  # empty -> all results
        size = write_csv(DUVAL_HEADERS, rows, LEADS_CSV)
        return jsonify({"status": "success", "bytes": size, "rows": len(rows), "message": "Fallback scrape filled CSV"})
    except Exception as e:
        app.logger.exception("refresh fallback failed")
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------------------------------------------------
# Routes: API - manual live scrape
# ------------------------------------------------------------------------------
@app.get("/api/scrape_now")
def api_scrape_now():
    try:
        q = (request.args.get("q") or "").strip()
        rows = scrape_duval(query_text=q)
        size = write_csv(DUVAL_HEADERS, rows, LEADS_CSV)
        return jsonify({"status": "success", "rows": len(rows), "bytes": size, "path": str(LEADS_CSV.resolve())})
    except Exception as e:
        app.logger.exception("scrape_now failed")
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # For local testing only; Render uses gunicorn via Procfile
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=True)
