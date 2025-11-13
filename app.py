import os
import json
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------
# Config – uses env vars if set, otherwise falls back to your values
# ---------------------------------------------------------------------
ALG_APP_ID = os.getenv("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALG_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX = os.getenv("ALG_INDEX", "fl-duval.property_tax")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LEADS_CSV = DATA_DIR / "leads.csv"

app = Flask(__name__, template_folder="templates", static_folder="static")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def call_algolia(query: str):
    """
    Call Algolia's search endpoint directly with requests.
    We search the fl-duval.property_tax index by whatever the user typed
    (account number, address, owner name, etc.).
    """
    url = f"https://{ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"

    headers = {
        "X-Algolia-Application-Id": ALG_APP_ID,
        "X-Algolia-API-Key": ALG_API_KEY,
        "Content-Type": "application/json",
    }

    # You can tune this later (filters, typoTolerance, etc.)
    params = f"query={query}&hitsPerPage=50"

    body = {
        "requests": [
            {
                "indexName": ALG_INDEX,
                "params": params,
            }
        ]
    }

    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
    resp.raise_for_status()
    return resp.json()


def hit_to_row(hit: dict) -> dict:
    """
    Map a raw Algolia hit to the fields your table expects.
    """
    # Address + zip (Algolia has multiple address structures, so we’re defensive)
    addresses = hit.get("addresses") or []
    first_addr = addresses[0] if addresses else {}

    address = (
        first_addr.get("address")
        or hit.get("address")
        or hit.get("situs_address")
        or ""
    )

    zip_code = (
        first_addr.get("zip")
        or hit.get("zip")
        or first_addr.get("postal_code")
        or ""
    )

    owner = (
        hit.get("display_name")
        or hit.get("owner_name")
        or hit.get("account_name")
        or ""
    )

    parcel = hit.get("account") or hit.get("parcel") or hit.get("account_number") or ""

    amount_due = hit.get("amount_due")
    try:
        amount_due = float(amount_due) if amount_due is not None else 0.0
    except (TypeError, ValueError):
        amount_due = 0.0

    link = (
        hit.get("public_url")
        or hit.get("link")
        or "https://county-taxes.net/fl-duval/property-tax"
    )

    return {
        "owner": owner,
        "address": address,
        "parcel": parcel,
        "zip": str(zip_code),
        "distress": "Tax",
        "amountDue": amount_due,
        "link": link,
    }


def perform_search_from_request():
    """
    Shared logic for /api/search and /api/search_tax.
    Uses only the `search` box for now; zip/min/max can be wired in later.
    """
    query = (request.args.get("search") or "").strip()
    if not query:
        return jsonify({"status": "success", "count": 0, "rows": []})

    try:
        raw = call_algolia(query)
        results = raw.get("results", [])
        hits = results[0].get("hits", []) if results else []

        rows = [hit_to_row(h) for h in hits]
        return jsonify({"status": "success", "count": len(rows), "rows": rows})
    except Exception as exc:
        # Log to Render console
        print("ERROR calling Algolia:", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search", methods=["GET"])
def api_search():
    return perform_search_from_request()


@app.route("/api/search_tax", methods=["GET"])
def api_search_tax():
    # Old JS might still call /api/search_tax – keep this for compatibility
    return perform_search_from_request()


@app.route("/api/health", methods=["GET"])
def api_health():
    """
    Simple health/debug endpoint.
    Shows Algolia settings and whether leads.csv exists.
    """
    csv_exists = LEADS_CSV.exists()
    csv_size = LEADS_CSV.stat().st_size if csv_exists else 0

    return jsonify(
        {
            "status": "success",
            "algolia_app_id": ALG_APP_ID,
            "algolia_index": ALG_INDEX,
            "csv_exists": csv_exists,
            "csv_size": csv_size,
        }
    )


# ---------------------------------------------------------------------
# For Render: gunicorn will use "app:app"
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
