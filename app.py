import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import csv
import requests
from datetime import datetime

# ======================================
# CONFIG
# ======================================

# Duval county Algolia (source tax data)
COUNTY_ALG_APP_ID = "0LWZO52LS2"
COUNTY_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
COUNTY_ALG_INDEX = "fl-duval.property_tax"

# Local CSV cache
CSV_FILE = "duval_data.csv"
CSV_FIELDS = [
    "parcel",
    "owner",
    "address",
    "zip",
    "amount_due",
    "distress",
    "link",
    "last_refreshed",
]

# ======================================
# FLASK APP
# ======================================

app = Flask(__name__)
CORS(app)


# ======================================
# CSV HELPERS
# ======================================

def load_csv_rows():
    if not os.path.exists(CSV_FILE):
        return []
    rows = []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def save_csv_rows(rows):
    os.makedirs(os.path.dirname(CSV_FILE) or ".", exist_ok=True)
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            out = {}
            for k in CSV_FIELDS:
                out[k] = r.get(k, "")
            writer.writerow(out)


def append_row_if_new(row):
    rows = load_csv_rows()
    existing = {r.get("parcel") for r in rows if r.get("parcel")}
    parcel = row.get("parcel")
    if parcel and parcel not in existing:
        rows.append(row)
        save_csv_rows(rows)


def parse_amount(value):
    if value is None:
        return 0.0
    s = str(value).replace("$", "").replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def filter_rows(rows, q=None, zip_code=None, min_amount=None, max_amount=None):
    q = (q or "").strip().lower()
    zip_code = (zip_code or "").strip()
    out = []
    for r in rows:
        if q:
            in_parcel = q in (r.get("parcel", "").lower())
            in_owner = q in (r.get("owner", "").lower())
            in_addr = q in (r.get("address", "").lower())
            if not (in_parcel or in_owner or in_addr):
                continue
        if zip_code and r.get("zip") != zip_code:
            continue
        amt = parse_amount(r.get("amount_due"))
        if min_amount is not None and amt < min_amount:
            continue
        if max_amount is not None and amt > max_amount:
            continue
        out.append(r)
    return out


# ======================================
# DUVAL LIVE SEARCH (RAW HTTP TO ALGOLIA)
# ======================================

def search_duval_live(query):
    """
    Hit Duval's Algolia index directly (no Algolia Python client).
    """
    if not query:
        return []

    url = f"https://{COUNTY_ALG_APP_ID}-dsn.algolia.net/1/indexes/{COUNTY_ALG_INDEX}/query"
    headers = {
        "X-Algolia-Application-Id": COUNTY_ALG_APP_ID,
        "X-Algolia-API-Key": COUNTY_ALG_API_KEY,
        "Content-Type": "application/json",
    }

    # This matches what you saw in DevTools: params string built with query + hitsPerPage
    params_str = f"query={query}&hitsPerPage=15"

    try:
        resp = requests.post(url, headers=headers, json={"params": params_str}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] Duval Algolia failed: {e}")
        return []

    hits = data.get("hits", [])
    rows = []
    now_iso = datetime.utcnow().isoformat()

    for h in hits:
        parcel = h.get("account") or h.get("parcel") or ""
        owner = h.get("owner_name") or h.get("owner") or ""
        address = h.get("situs_address") or h.get("address") or ""
        zip_code = str(h.get("zip", "") or "")
        amount_due = h.get("amount_due") or h.get("amountDue") or 0
        public_url = h.get("public_url") or h.get("link") or ""

        row = {
            "parcel": parcel,
            "owner": owner,
            "address": address,
            "zip": zip_code,
            "amount_due": str(amount_due),
            "distress": "Tax",
            "link": public_url,
            "last_refreshed": now_iso,
        }
        rows.append(row)

    return rows


# ======================================
# API ROUTES
# ======================================

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    zip_code = request.args.get("zip", "").strip()
    min_amount_raw = request.args.get("min_amount", "").strip()
    max_amount_raw = request.args.get("max_amount", "").strip()

    min_amount = parse_amount(min_amount_raw) if min_amount_raw else None
    max_amount = parse_amount(max_amount_raw) if max_amount_raw else None

    # 1. Try CSV cache
    csv_rows = load_csv_rows()
    rows = filter_rows(csv_rows, q=q, zip_code=zip_code,
                       min_amount=min_amount, max_amount=max_amount)

    # 2. If nothing from cache and we have a query, hit Duval live
    if not rows and q:
        live_rows = search_duval_live(q)
        for r in live_rows:
            append_row_if_new(r)
        # Filter again in case we care about zip/amount filters
        rows = filter_rows(live_rows, q=q, zip_code=zip_code,
                           min_amount=min_amount, max_amount=max_amount)

    return jsonify({
        "status": "success",
        "count": len(rows),
        "rows": rows,
    })


@app.route("/api/export")
def api_export():
    rows = load_csv_rows()
    return jsonify({
        "status": "success",
        "count": len(rows),
        "rows": rows,
    })


@app.route("/api/health")
def api_health():
    rows = load_csv_rows()
    return jsonify({
        "status": "success",
        "csv_exists": os.path.exists(CSV_FILE),
        "csv_size": len(rows),
    })


@app.route("/")
def root():
    return "Distress Intelligence backend is online."


if __name__ == "__main__":
    # For local dev; Render will use gunicorn with app:app
    app.run(host="0.0.0.0", port=10000)
