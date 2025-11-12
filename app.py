import os, csv, io, json, re, logging, pathlib
from flask import Flask, request, send_from_directory, jsonify
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

APP_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
LEADS_PATH = DATA_DIR / "leads.csv"

# --------- CONFIG ---------
GH_RAW_URL = os.getenv("GH_RAW_URL", "").strip()
ALG_APP_ID = "0LWZO52LS2"
ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"  # public search key (from your DevTools)
ALG_INDEX = "fl-duval.property_tax"
ALG_ENDPOINT = f"https://{ALG_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

# Canonical CSV headers your UI expects:
HEADERS = ["owner", "property_address", "parcel", "zip", "distress", "amount_due"]

app = Flask(__name__, static_folder=str(APP_ROOT), static_url_path="")

# ------------- helpers -------------
def safe_float(x):
    try:
        # strip $, commas, spaces
        return float(re.sub(r"[^0-9.\-]", "", str(x)))
    except Exception:
        return 0.0

def ensure_headers(path: pathlib.Path):
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADERS)

def csv_rows_count(path: pathlib.Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        return max(0, sum(1 for _ in f) - 1)  # minus header

def write_rows(rows):
    ensure_headers(LEADS_PATH)
    with LEADS_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)

def normalize_hit(hit: dict) -> list:
    """
    Map many possible field names from the county Algolia index
    into our 6-column CSV. Anything missing becomes '' or 0.
    """
    owner = hit.get("owner") or hit.get("owner_name") or hit.get("name") or ""
    addr = (
        hit.get("address")
        or hit.get("property_address")
        or hit.get("situs_address")
        or hit.get("mailing_address")
        or hit.get("address_line")
        or ""
    )
    parcel = (
        hit.get("parcel")
        or hit.get("parcel_id")
        or hit.get("account")
        or hit.get("account_number")
        or ""
    )
    # zip/postal
    zipc = (
        hit.get("zip")
        or hit.get("postal_code")
        or hit.get("zipcode")
        or hit.get("zip_code")
        or ""
    )
    # amount due
    due = (
        hit.get("amount_due")
        or hit.get("total_due")
        or hit.get("balance")
        or hit.get("balance_due")
        or hit.get("amount")
        or 0
    )
    distress = "Tax"
    return [
        str(owner).strip(),
        str(addr).strip(),
        str(parcel).strip(),
        str(zipc).strip(),
        distress,
        f"{safe_float(due):.2f}",
    ]

def fetch_github_csv(raw_url: str) -> bytes:
    if not raw_url:
        raise ValueError("GH_RAW_URL not set")
    resp = requests.get(raw_url, timeout=30)
    resp.raise_for_status()
    return resp.content

def refresh_from_github() -> dict:
    try:
        blob = fetch_github_csv(GH_RAW_URL)
        # guard against accidental newline suffixes in env var
        if GH_RAW_URL.endswith("%0A") or GH_RAW_URL.endswith("\n"):
            raise ValueError("GH_RAW_URL contains a newline. Edit the env var to remove it.")
        # Make sure the file has a header; if not, prepend our header
        content = blob.decode("utf-8", errors="ignore")
        if not content.strip():
            raise ValueError("GitHub CSV is empty")
        # If it already has our header, just write through
        if content.splitlines()[0].lower().replace(" ", "_") \
            .startswith(",".join(HEADERS).split(",")[0]):
            LEADS_PATH.write_text(content, encoding="utf-8")
        else:
            # Try to keep what’s there, but ensure headers exist
            ensure_headers(LEADS_PATH)
            with LEADS_PATH.open("a", newline="", encoding="utf-8") as f:
                f.write(content if content.endswith("\n") else content + "\n")
        return {"status":"success","source":GH_RAW_URL,"bytes":LEADS_PATH.stat().st_size}
    except Exception as e:
        return {"status":"error","message":str(e)}

def refresh_from_algolia(limit=1000) -> dict:
    """
    Use public search to pull up to `limit` hits.
    We query with empty string to return recent/popular items.
    """
    headers = {
        "x-algolia-application-id": ALG_APP_ID,
        "x-algolia-api-key": ALG_API_KEY,
        "Content-Type": "application/json",
        "x-algolia-agent": "python(custom) instantsearch.js(4)"
    }
    # Algolia limits hitsPerPage to <=1000. We'll take one page with 1000.
    params = {
        "clickAnalytics": False,
        "facets": [],
        "highlightPostTag": "__/ais-highlight__",
        "highlightPreTag": "__ais-highlight__",
        "hitsPerPage": min(1000, int(limit)),
        "query": "",            # blank = match many
        "tagFilters": ""
    }
    payload = {"requests":[{"indexName": ALG_INDEX, "params": "&".join(f"{k}={json.dumps(v) if isinstance(v,(dict,list)) else v}" for k,v in params.items())}]}
    r = requests.post(ALG_ENDPOINT, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    hits = (data.get("results") or [{}])[0].get("hits") or []
    if not hits:
        return {"status":"error","message":"Algolia returned 0 hits"}
    rows = [normalize_hit(h) for h in hits]
    # filter nonsense rows that end up completely empty
    rows = [r for r in rows if any(c.strip() for c in r)]
    write_rows(rows)
    return {"status":"success","rows":len(rows),"bytes":LEADS_PATH.stat().st_size}

def file_exists_info(path: pathlib.Path) -> dict:
    return {"exists": path.exists(), "path": str(path), "size": path.stat().st_size if path.exists() else 0}

# ------------- routes -------------
@app.route("/")
def index():
    # Serve index.html from repo root
    path = APP_ROOT / "index.html"
    if not path.exists():
        return ("Not Found", 404)
    log.info("Serving index. cwd=%s root=%s exists=%s", APP_ROOT, path, path.exists())
    return send_from_directory(str(APP_ROOT), "index.html")

@app.route("/data/<path:filename>")
def data_files(filename):
    return send_from_directory(str(DATA_DIR), filename)

@app.route("/api/leads")
def api_leads():
    ensure_headers(LEADS_PATH)
    # read and filter
    zip_filter = (request.args.get("zip") or "").strip()
    min_amount = safe_float(request.args.get("min_amount"))
    max_amount = safe_float(request.args.get("max_amount")) if request.args.get("max_amount") else None
    srcs = [s.strip().lower() for s in (request.args.get("sources") or "tax").split(",") if s.strip()]

    out = []
    with LEADS_PATH.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if not row: 
                continue
            if srcs and (row.get("distress","").lower() not in srcs):
                continue
            if zip_filter and str(row.get("zip","")).strip() != zip_filter:
                continue
            due = safe_float(row.get("amount_due", 0))
            if min_amount and due < min_amount:
                continue
            if max_amount and max_amount > 0 and due > max_amount:
                continue
            out.append(row)
    return jsonify({"status":"success","count":len(out),"rows":out[:1000]})

@app.route("/api/refresh")
def api_refresh():
    """
    1) Try GitHub raw CSV (if env is set and accessible).
    2) If after that the file has < 5 rows, fall back to Algolia.
    """
    results = {}
    if GH_RAW_URL:
        results["github"] = refresh_from_github()
    else:
        results["github"] = {"status":"skipped","message":"GH_RAW_URL not set"}

    rows = csv_rows_count(LEADS_PATH)
    if rows < 5:
        try:
            results["algolia"] = refresh_from_algolia(limit=1000)
        except Exception as e:
            results["algolia"] = {"status":"error","message":str(e)}

    results["final"] = {"rows": csv_rows_count(LEADS_PATH), "bytes": file_exists_info(LEADS_PATH)["size"]}
    return jsonify(results)

# tiny debug helpers
@app.route("/debug/file/<path:relpath>")
def debug_file(relpath):
    p = (APP_ROOT / relpath)
    return jsonify(file_exists_info(p))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
