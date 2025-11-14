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

# Make sure data folder & CSV exist
DATA_DIR.mkdir(exist_ok=True)
CSV_HEADERS = ["address", "zip", "parcel", "distress", "amountDue", "owner", "link"]
if not CSV_FILE.exists():
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()

# Algolia settings – will use your env vars if set, otherwise Duval defaults
ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID", "0LWZO52LS2")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY", "")
ALGOLIA_INDEX = os.getenv("ALGOLIA_INDEX", "fl-duval.property_tax")

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)


def read_existing_parcels() -> Set[str]:
    """Return a set of parcel/account numbers already stored in the CSV."""
    parcels: Set[str] = set()
    if not CSV_FILE.exists():
        return parcels
    with CSV_FILE.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parcel = (row.get("parcel") or "").strip()
            if parcel:
                parcels.add(parcel)
    return parcels


def load_all_rows() -> List[Dict[str, Any]]:
    """Load all rows from CSV with amountDue normalized to float."""
    rows: List[Dict[str, Any]] = []
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
    """Append new rows to CSV but skip any parcel that already exists."""
    if not new_rows:
        return 0
    existing_parcels = read_existing_parcels()
    unique_rows: List[Dict[str, Any]] = []
    for r in new_rows:
        parcel = (r.get("parcel") or "").strip()
        if parcel and parcel not in existing_parcels:
            existing_parcels.add(parcel)
            unique_rows.append(r)

    if not unique_rows:
        return 0

    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        for r in unique_rows:
            out = {h: "" for h in CSV_HEADERS}
            out.update({k: ("" if v is None else v) for k, v in r.items() if k in CSV_HEADERS})
            writer.writerow(out)
    return len(unique_rows)


def normalize_algolia_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one Algolia hit into our CSV row format."""
    parcel = hit.get("account") or hit.get("account_number") or ""
    owner = hit.get("display_name") or hit.get("owner_name") or ""

    addr_obj = hit.get("address") or {}
    address = (
        addr_obj.get("address")
        or addr_obj.get("name")
        or addr_obj.get("line1")
        or ""
    )
    zip_code = addr_obj.get("zip") or addr_obj.get("postalCode") or addr_obj.get("postcode") or ""

    amount = hit.get("amount_due") or hit.get("amountDue") or 0
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0

    link = hit.get("public_url") or "https://county-taxes.net/fl-duval/property-tax"

    return {
        "address": address,
        "zip": str(zip_code) if zip_code is not None else "",
        "parcel": str(parcel) if parcel is not None else "",
        "distress": "Tax",
        "amountDue": amount,
        "owner": owner,
        "link": link,
    }


def query_algolia(q: str) -> List[Dict[str, Any]]:
    """Call Algolia directly via HTTP and return normalized rows."""
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
                "params": f"hitsPerPage=50&clickAnalytics=false&query={q}",
            }
        ]
    }

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return []
        hits = results[0].get("hits") or []
        return [normalize_algolia_hit(h) for h in hits]
    except Exception:
        # Fail silently — API downtime shouldn't crash your SaaS
        return []


def filter_rows(
    rows: List[Dict[str, Any]],
    q: Optional[str],
    zip_code: Optional[str],
    min_amount: Optional[float],
    max_amount: Optional[float],
) -> List[Dict[str, Any]]:
    """Apply text, zip and amount filters to a list of rows."""
    q = (q or "").strip()
    zip_code = (zip_code or "").strip()
    results: List[Dict[str, Any]] = []

    for row in rows:
        if q:
            haystack = f"{row.get('parcel','')} {row.get('address','')} {row.get('owner','')}".lower()
            if q.lower() not in haystack:
                continue

        if zip_code and str(row.get("zip", "")).strip() != zip_code:
            continue

        amt = row.get("amountDue") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0.0

        if min_amount is not None and amt < min_amount:
            continue
        if max_amount is not None and amt > max_amount:
            continue

        results.append(row)

    return results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    zip_code = request.args.get("zip", "").strip()
    min_amount_str = request.args.get("minAmount", "").strip()
    max_amount_str = request.args.get("maxAmount", "").strip()

    min_amount = float(min_amount_str) if min_amount_str else None
    max_amount = float(max_amount_str) if max_amount_str else None

    # 1) Try local CSV first
    local_rows = load_all_rows()
    rows = filter_rows(local_rows, q, zip_code, min_amount, max_amount)

    # 2) If nothing found, call Algolia and cache new rows
    if not rows and q:
        algolia_rows = query_algolia(q)
        save_new_rows_no_duplicates(algolia_rows)
        local_rows = load_all_rows()
        rows = filter_rows(local_rows, q, zip_code, min_amount, max_amount)

    return jsonify({"count": len(rows), "rows": rows, "status": "success"})


@app.route("/api/health")
def api_health():
    csv_exists = CSV_FILE.exists()
    csv_size = CSV_FILE.stat().st_size if csv_exists else 0
    return jsonify(
        {
            "algolia_app_id": ALGOLIA_APP_ID,
            "algolia_index": ALGOLIA_INDEX,
            "csv_exists": csv_exists,
            "csv_size": csv_size,
            "status": "success",
        }
    )


@app.route("/api/export-info")
def api_export_info():
    if not CSV_FILE.exists():
        return jsonify(
            {
                "exists": False,
                "path": str(CSV_FILE),
                "size": 0,
                "status": "success",
            }
        )
    return jsonify(
        {
            "exists": True,
            "path": str(CSV_FILE),
            "size": CSV_FILE.stat().st_size,
            "status": "success",
        }
    )


@app.route("/api/export")
def api_export():
    if not CSV_FILE.exists():
        return jsonify({"error": "CSV not found", "status": "error"}), 404
    return send_file(
        CSV_FILE,
        mimetype="text/csv",
        as_attachment=True,
        download_name="leads.csv",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
