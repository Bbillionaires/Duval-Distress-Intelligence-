import os, re, csv, json, pathlib, logging
from flask import Flask, request, jsonify, send_from_directory
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

APP_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR  = (APP_ROOT / "data"); DATA_DIR.mkdir(exist_ok=True)
LEADS_CSV = DATA_DIR / "leads.csv"

# === ENV / CONFIG ===
GH_RAW_URL = (os.getenv("GH_RAW_URL","").strip())
ALG_APP_ID = "0LWZO52LS2"
ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"     # public search key (ok to ship)
ALG_INDEX   = "fl-duval.property_tax"
ALG_ENDPOINT = f"https://{ALG_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

# EXACT headers that your CSV/export shows:
HEADERS = ["address","zip","parcel","distress","amountDue","owner"]

app = Flask(__name__, static_folder=str(APP_ROOT), static_url_path="")

# ---------- helpers ----------
def ensure_headers():
    if not LEADS_CSV.exists() or LEADS_CSV.stat().st_size == 0:
        with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADERS)

def csv_rows_count():
    if not LEADS_CSV.exists() or LEADS_CSV.stat().st_size == 0:
        return 0
    with LEADS_CSV.open("r", encoding="utf-8", newline="") as f:
        return max(0, sum(1 for _ in f) - 1)

def clean_amount(x):
    if x is None: return 0.0
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(x)) or 0)
    except Exception:
        return 0.0

def first(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and v != 0:
            return v
    return ""

def join_addr(*parts):
    parts = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(dict.fromkeys(parts))  # dedupe while preserving order

def normalize_hit(hit: dict):
    """
    Map MANY possible field names from Algolia → our 6 columns.
    If a field is missing, keep it empty so the CSV is consistent.
    """
    # address candidates
    addr = first(
        hit.get("property_address"),
        hit.get("situs_address"),
        hit.get("situs_addr1"),
        hit.get("address"),
        join_addr(hit.get("situs_addr1"), hit.get("situs_city"), hit.get("situs_state")),
        join_addr(hit.get("address_line"), hit.get("city"), hit.get("state"))
    )

    # zip candidates (postal)
    zipc = first(
        hit.get("zip"),
        hit.get("postal_code"),
        hit.get("zipcode"),
        hit.get("zip_code"),
        hit.get("situs_zip"),
        hit.get("mailing_zip"),
    )

    # parcel / account
    parcel = first(
        hit.get("parcel"),
        hit.get("parcel_id"),
        hit.get("account"),
        hit.get("account_number"),
        hit.get("folio"),
    )

    # owner name(s)
    owner = first(
        hit.get("owner"),
        hit.get("owner_name"),
        hit.get("name"),
        hit.get("owner1"),
        hit.get("owner2"),
        join_addr(hit.get("owner1"), hit.get("owner2")),
    )

    # amount due (try multiple fields)
    amount = first(
        hit.get("amount_due"),
        hit.get("total_due"),
        hit.get("balance_due"),
        hit.get("balance"),
        hit.get("tax_due"),
        hit.get("amount"),
        hit.get("unpaid_balance"),
        hit.get("current_due"),
        hit.get("delinquent_amount"),
        hit.get("totalDue"),
        hit.get("amountDue"),
    )
    amount = f"{clean_amount(amount):.2f}"

    return [
        addr,                         # address
        str(zipc),                    # zip
        str(parcel),                  # parcel
        "Tax",                        # distress (this feed is tax)
        amount,                       # amountDue
        owner,                        # owner
    ]

def write_rows(rows):
    ensure_headers()
    with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)

def fetch_github_csv():
    if not GH_RAW_URL or GH_RAW_URL.endswith("%0A"):
        raise ValueError("GH_RAW_URL is empty or contains a newline (%0A). Edit the env var.")
    r = requests.get(GH_RAW_URL, timeout=30)
    r.raise_for_status()
    return r.text

def refresh_from_github():
    try:
        text = fetch_github_csv()
        if not text.strip():
            return {"status":"error","message":"GitHub CSV empty"}
        # If header doesn't match our HEADERS, we still write what we received.
        with LEADS_CSV.open("w", encoding="utf-8", newline="") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        return {"status":"success","source":"github","bytes":LEADS_CSV.stat().st_size}
    except Exception as e:
        return {"status":"error","message":str(e)}

def refresh_from_algolia(limit=1000):
    headers = {
        "x-algolia-application-id": ALG_APP_ID,
        "x-algolia-api-key": ALG_API_KEY,
        "Content-Type": "application/json",
        "x-algolia-agent": "python(custom)"
    }
    params = {
        "clickAnalytics": False,
        "facets": [],
        "hitsPerPage": min(1000, int(limit)),
        "query": "",         # broad query
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
    # remove totally empty rows
    rows = [r for r in rows if any(c.strip() for c in r)]
    write_rows(rows)
    return {"status":"success","rows":len(rows),"bytes":LEADS_CSV.stat().st_size}

# ---------- routes ----------
@app.route("/")
def serve_index():
    path = APP_ROOT / "index.html"
    if not path.exists():
        return ("Not Found", 404)
    return send_from_directory(str(APP_ROOT), "index.html")

@app.route("/data/<path:filename>")
def serve_data(filename):
    return send_from_directory(str(DATA_DIR), filename)

@app.route("/api/refresh")
def api_refresh():
    results = {}
    # 1) GitHub (if set)
    if GH_RAW_URL:
        results["github"] = refresh_from_github()
    else:
        results["github"] = {"status":"skipped","message":"GH_RAW_URL not set"}

    # 2) If we still have < 5 rows or amounts look empty, pull Algolia
    if csv_rows_count() < 5:
        results["algolia"] = refresh_from_algolia(1000)

    results["final"] = {"rows": csv_rows_count(), "bytes": LEADS_CSV.stat().st_size if LEADS_CSV.exists() else 0}
    return jsonify(results)

@app.route("/api/leads")
def api_leads():
    ensure_headers()
    zip_filter = (request.args.get("zip") or "").strip()
    min_amount = clean_amount(request.args.get("min_amount"))
    max_amount = clean_amount(request.args.get("max_amount")) if request.args.get("max_amount") else None
    sources = [s.strip().lower() for s in (request.args.get("sources") or "tax").split(",") if s.strip()]

    out = []
    with LEADS_CSV.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        # accept both old/new header variants just in case
        for row in r:
            # normalize accessors
            distress = (row.get("distress") or row.get("source") or "").lower()
            if sources and ("tax" not in sources) and (distress not in sources):
                continue
            if zip_filter and (str(row.get("zip","")).strip() != zip_filter):
                continue
            due = clean_amount(row.get("amountDue") or row.get("amount_due"))
            if min_amount and due < min_amount:
                continue
            if max_amount and max_amount > 0 and due > max_amount:
                continue
            out.append({
                "address": row.get("address",""),
                "zip": row.get("zip",""),
                "parcel": row.get("parcel",""),
                "distress": row.get("distress",""),
                "amountDue": f"{due:.2f}",
                "owner": row.get("owner",""),
            })
    return jsonify({"status":"success","count":len(out),"rows":out[:1000]})

@app.route("/debug/file/<path:rel>")
def debug_file(rel):
    p = (APP_ROOT / rel)
    return jsonify({"exists": p.exists(), "path": str(p), "size": p.stat().st_size if p.exists() else 0})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
