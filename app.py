import os
import csv
from pathlib import Path
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS
from algoliasearch.search_client import SearchClient

# --------------------------------------------------
# Flask setup
# --------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# --------------------------------------------------
# Paths for CSV storage
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "leads.csv"

# --------------------------------------------------
# Algolia setup
# --------------------------------------------------
ALG_APP_ID = os.getenv("ALGOLIA_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALGOLIA_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX = os.getenv("ALGOLIA_INDEX", "fl-duval.property_tax")

# Create client/index once (Render will reuse the worker)
alg_client = SearchClient.create(ALG_APP_ID, ALG_API_KEY)
alg_index = alg_client.init_index(ALG_INDEX)


# Helper to safely parse float
def parse_float(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# --------------------------------------------------
# API: search distress via Algolia
# --------------------------------------------------
@app.route("/api/search")
def api_search():
    """
    Query parameters:
      q          - search text (account number, owner, address, etc)
      zip        - optional zip code
      min_amount - optional minimum amount due
      max_amount - optional maximum amount due
    """
    q = (request.args.get("q") or "").strip()
    zip_code = (request.args.get("zip") or "").strip()
    min_amount = parse_float(request.args.get("min_amount"))
    max_amount = parse_float(request.args.get("max_amount"))

    # Build Algolia search params
    params = {
        "hitsPerPage": 1000,  # grab up to 1000 records for export
    }

    filters = []

    # numeric filters on amount_due
    if min_amount is not None:
        filters.append(f"amount_due >= {min_amount}")
    if max_amount is not None:
        filters.append(f"amount_due <= {max_amount}")

    # zip filter (if this attribute exists as a numeric or string facet)
    if zip_code:
        # If zip is stored as string facet
        filters.append(f"zip:{zip_code}")

    if filters:
        # Join all filters with AND
        params["filters"] = " AND ".join(filters)

    # Call Algolia – note: query string is first arg, params is second
    # If q is empty, Algolia will still return hits (browse)
    res = alg_index.search(q or "", params)
    hits = res.get("hits", [])

    rows = []
    for hit in hits:
        # Try multiple possible field names based on what we saw
        owner = (
            hit.get("owner")
            or hit.get("owner_name")
            or hit.get("display_name")
            or ""
        )
        address = (
            hit.get("address")
            or hit.get("situs_address")
            or hit.get("situsAddress")
            or ""
        )
        parcel = (
            hit.get("account")
            or hit.get("account_number")
            or hit.get("parcel")
            or ""
        )
        zip_val = str(hit.get("zip") or "").strip()

        amount_due = hit.get("amount_due") or hit.get("amountDue") or 0
        try:
            amount_due = float(amount_due)
        except (TypeError, ValueError):
            amount_due = 0.0

        rows.append(
            {
                "owner": owner,
                "address": address,
                "parcel": parcel,
                "zip": zip_val,
                "distress": "Tax",  # this index is tax-only
                "amountDue": amount_due,
            }
        )

    # Write to CSV for export
    fieldnames = ["owner", "address", "parcel", "zip", "distress", "amountDue"]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return jsonify({"count": len(rows), "rows": rows, "status": "success"})


# --------------------------------------------------
# API: export current leads.csv
# --------------------------------------------------
@app.route("/api/export")
def api_export():
    if not CSV_PATH.exists():
        # Nothing searched yet
        return jsonify(
            {"error": "No CSV generated yet. Run a search first.", "status": "error"}
        ), 400

    return send_file(
        CSV_PATH,
        as_attachment=True,
        download_name="leads.csv",
        mimetype="text/csv",
    )


# --------------------------------------------------
# API: health check / debug
# --------------------------------------------------
@app.route("/api/health")
def api_health():
    exists = CSV_PATH.exists()
    size = CSV_PATH.stat().st_size if exists else 0

    return jsonify(
        {
            "algolia_app_id": ALG_APP_ID,
            "algolia_index": ALG_INDEX,
            "csv_exists": exists,
            "csv_size": size,
            "status": "success",
        }
    )


# --------------------------------------------------
# Frontend
# --------------------------------------------------
@app.route("/")
def index():
    # Renders templates/index.html
    return render_template("index.html")


# --------------------------------------------------
# Main (local dev)
# --------------------------------------------------
if __name__ == "__main__":
    # For local testing; Render will use gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
