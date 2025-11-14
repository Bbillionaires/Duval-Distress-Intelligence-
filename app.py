import os
import csv
from pathlib import Path
from typing import List, Dict, Any, Set, Optional

import requests
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_FILE = DATA_DIR / "leads.csv"

# Ensure data folder exists
DATA_DIR.mkdir(exist_ok=True)

CSV_HEADERS = ["address", "zip", "parcel", "distress", "amountDue", "owner", "link"]

if not CSV_FILE.exists():
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()

ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID", "0LWZO52LS2")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY", "")
ALGOLIA_INDEX = os.getenv("ALGOLIA_INDEX", "fl-duval.property_tax")

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)


def read_existing_parcels() -> Set[str]:
    parcels = set()
    if not CSV_FILE.exists():
        return parcels

    with CSV_FILE.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p = (row.get("parcel") or "").strip()
            if p:
                parcels.add(p)
    return parcels


def load_all_rows() -> List[Dict[str, Any]]:
    rows = []
    if not CSV_FILE.exists():
        return rows

    with CSV_FILE.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["amountDue"] = float(row.get("amountDue") or 0)
            except ValueError:
                row["amountDue"] = 0.0
            rows.append(row)
    return rows


def save_new_rows_no_duplicates(new_rows: List[Dict[str, Any]]) -> int:
    if not new_rows:
        return 0

    existing = read_existing_parcels()
    unique = []

    for r in new_rows:
        parcel = (r.get("parcel") or "").strip()
        if parcel and parcel not in existing:
            existing.add(parcel)
            unique.append(r)

    if not unique:
        return 0

    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        for row in unique:
            output = {h: "" for h in CSV_HEADERS}
            output.update(row)
            writer.writerow(output)

    return len(unique)


def normalize_algolia_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    parcel = hit.get("account") or hit.get("account_number") or ""
    owner = hit.get("display_name") or hit.get("owner_name") or ""
    addr = hit.get("address") or {}

    address = (
        addr.get("address") or addr.get("name") or addr.get("line1") or ""
    )
    zip_code = (
        addr.get("zip") or addr.get("postalCode") or addr.get("postcode") or ""
    )

    amount = hit.get("amount_due") or 0
    try:
        amount = float(amount)
    except:
        amount = 0.0

    link = hit.get("public_url") or "https://county-taxes.net/fl-duval/property-tax"

    return {
        "address": address,
        "zip": str(zip_code),
        "parcel": str(parcel),
        "distress": "Tax",
        "amountDue": amount,
        "owner": owner,
        "link": link,
    }


def query_algolia(q: str) -> List[Dict[str, Any]]:
    if not ALGOLIA_API_KEY:
        return []

    endpoint = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "requests": [
            {
                "indexName": ALGOLIA_INDEX,
                "params": f"hitsPerPage=50&query={q}",
            }
        ]
    }

    try:
        r = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        data = r.json()
        hits = data.get("results", [{}])[0].get("hits", [])
        return [normalize_algolia_hit(h) for h in hits]
    except:
        return []


def filter_rows(rows, q, zip_code, min_amount, max_amount):
    q = (q or "").lower().strip()
    zip_code = (zip_code or "").strip()

    out = []
    for row in rows:
        hay = f"{row.get('parcel','')} {row.get('address','')} {row.get('owner','')}".lower()

        if q and q not in hay:
            continue
        if zip_code and str(row.get("zip", "")) != zip_code:
            continue

        amt = float(row.get("amountDue", 0))

        if min_amount is not None and amt < min_amount:
            continue
        if max_amount is not None and amt > max_amount:
            continue

        out.append(row)

    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    zip_code = request.args.get("zip", "").strip()

    min_amount = request.args.get("minAmount")
    min_amount = float(min_amount) if min_amount else None

    max_amount = request.args.get("maxAmount")
    max_amount = float(max_amount) if max_amount else None

    rows = filter_rows(load_all_rows(), q, zip_code, min_amount, max_amount)

    if not rows and q:
        new_hits = query_algolia(q)
        save_new_rows_no_duplicates(new_hits)
        rows = filter_rows(load_all_rows(), q, zip_code, min_amount, max_amount)

    return jsonify({"count": len(rows), "rows": rows, "status": "success"})


@app.route("/api/health")
def api_health():
    exists = CSV_FILE.exists()
    size = CSV_FILE.stat().st_size if exists else 0
    return jsonify({
        "algolia_app_id": ALGOLIA_APP_ID,
        "algolia_index": ALGOLIA_INDEX,
        "csv_exists": exists,
        "csv_size": size,
        "status": "success",
    })


@app.route("/api/export-info")
def api_export_info():
    exists = CSV_FILE.exists()
    size = CSV_FILE.stat().st_size if exists else 0
    return jsonify({
        "exists": exists,
        "size": size,
        "path": str(CSV_FILE),
        "status": "success",
    })


@app.route("/api/export")
def api_export():
    if not CSV_FILE.exists():
        return jsonify({"error": "CSV not found"}), 404
    return send_file(
        CSV_FILE,
        mimetype="text/csv",
        as_attachment=True,
        download_name="leads.csv",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
