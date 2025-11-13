import os
import json
import logging
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------
# Basic setup
# ---------------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# CSV is no longer used by the UI, but we'll keep the path if you want later.
CSV_PATH = DATA_DIR / "leads.csv"

# ---------------------------------------------------------
# Algolia configuration
# ---------------------------------------------------------

ALG_APP_ID = os.getenv("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALG_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX_NAME = os.getenv("ALG_INDEX_NAME", "fl-duval.property_tax")

ALG_SEARCH_URL = (
    f"https://{ALG_APP_ID}-dsn.algolia.net/1/indexes/{ALG_INDEX_NAME}/query"
)

ALG_HEADERS = {
    "x-algolia-application-id": ALG_APP_ID,
    "x-algolia-api-key": ALG_API_KEY,
    "x-algolia-agent": "distress-intelligence-backend (python)",
    "Content-Type": "application/json",
}

COUNTY_TAX_URL = "https://county-taxes.net/fl-duval/property-tax"

# ---------------------------------------------------------
# Helpers to walk Algolia hits
# ---------------------------------------------------------


def _flatten(obj, parent_key=""):
    """Flatten nested dict/list into a list of (path, value) pairs."""
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}.{k}" if parent_key else k
            items.extend(_flatten(v, new_key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_key = f"{parent_key}[{i}]"
            items.extend(_flatten(v, new_key))
    else:
        items.append((parent_key, obj))
    return items


def _find_first(flat_items, predicate):
    for key, value in flat_items:
        try:
            if predicate(key, value):
                return value
        except Exception:
            continue
    return None


def extract_fields_from_hit(hit: dict) -> dict:
    """
    Try to pull owner, address, parcel, zip, amountDue from a single Algolia hit.
    This is defensive: it scans all nested keys instead of assuming a fixed shape.
    """
    flat = _flatten(hit)

    # Owner: display_name or owner_name style fields
    owner = _find_first(
        flat,
        lambda k, v: isinstance(v, str)
        and any(t in k.lower() for t in ["owner_name", "display_name"]),
    )
    if not owner and isinstance(hit.get("display_name"), str):
        owner = hit["display_name"]

    # Parcel / account number
    parcel = _find_first(
        flat,
        lambda k, v: isinstance(v, str)
        and ("external_id" in k.lower() or "account" in k.lower())
        and "-" in v,
    )
    if not parcel:
        # fallback: any value that looks like 030147-0432
        parcel = _find_first(
            flat,
            lambda k, v: isinstance(v, str)
            and "-" in v
            and v.replace("-", "").isdigit(),
        )

    # Address – prefer situs / property address
    address = _find_first(
        flat,
        lambda k, v: isinstance(v, str)
        and any(t in k.lower() for t in ["situs_address", "property_address", "address"])
        and any(c.isalpha() for c in v),
    )

    # Zip / postal code
    zip_code = _find_first(
        flat,
        lambda k, v: isinstance(v, str)
        and any(t in k.lower() for t in ["postal", "zip"])
        and v.replace("-", "").isdigit(),
    )

    # Amount due – any numeric field with "amount" in its key
    amount_due = _find_first(
        flat,
        lambda k, v: isinstance(v, (int, float)) and "amount" in k.lower(),
    )
    if amount_due is None:
        amount_due = 0.0

    row = {
        "owner": owner or "",
        "parcel": parcel or "",
        "address": address or (parcel or ""),
        "zip": zip_code or "",
        "amountDue": float(amount_due),
        "distress": "Tax",
        # 👉 ALWAYS point users to the main property-tax page
        "link": COUNTY_TAX_URL,
    }

    log.info("EXTRACTED ROW: %s", row)
    return row


def algolia_search(account: str):
    """
    Query Algolia for a single account / parcel and return a normalized row dict,
    or None if nothing found.
    """
    params = f"query={account}&hitsPerPage=5"
    payload = {"params": params}

    log.info("Algolia search for account=%s", account)
    resp = requests.post(
        ALG_SEARCH_URL, headers=ALG_HEADERS, json=payload, timeout=10
    )
    resp.raise_for_status()
    data = resp.json()

    hits = data.get("hits", [])
    if not hits:
        log.info("No hits from Algolia for %s", account)
        return None, data

    first_hit = hits[0]
    log.info("FIRST HIT KEYS: %s", list(first_hit.keys()))
    row = extract_fields_from_hit(first_hit)
    return row, data


# ---------------------------------------------------------
# Flask app + routes
# ---------------------------------------------------------

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app)


@app.route("/")
def serve_index():
    """Serve the SPA index.html."""
    index_path = BASE_DIR / "index.html"
    log.info("Serving index. exists=%s path=%s", index_path.exists(), index_path)
    return send_from_directory(str(BASE_DIR), "index.html")


@app.route("/api/health")
def api_health():
    """Basic health check + whether CSV exists (for debugging)."""
    exists = CSV_PATH.exists()
    size = CSV_PATH.stat().st_size if exists else 0
    return jsonify(
        {
            "status": "success",
            "csv_exists": exists,
            "csv_size": size,
            "algolia_app_id": ALG_APP_ID,
            "algolia_index": ALG_INDEX_NAME,
        }
    )


@app.route("/api/debug/algolia")
def api_debug_algolia():
    """
    Debug endpoint:
    /api/debug/algolia?account=030147-0432
    Returns the raw Algolia response plus the normalized row.
    """
    account = request.args.get("account", "").strip()
    if not account:
        return jsonify({"status": "error", "message": "Missing account param"}), 400

    try:
        row, raw = algolia_search(account)
        return jsonify(
            {
                "status": "success",
                "account": account,
                "row": row,
                "raw": raw,
            }
        )
    except Exception as e:
        log.exception("Algolia debug failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/search")
def api_search():
    """
    Main search used by the UI.

    Accepts:
      - search / q / account: the tax account or parcel number
      - zip (optional)
      - minAmountDue, maxAmountDue (optional, floats)
    """
    account = (
        request.args.get("search")
        or request.args.get("q")
        or request.args.get("account")
        or ""
    ).strip()

    zip_filter = request.args.get("zip", "").strip()

    def _to_float(name):
        val = request.args.get(name)
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    min_amount = _to_float("minAmountDue")
    max_amount = _to_float("maxAmountDue")

    log.info(
        "API /api/search account=%s zip=%s min=%s max=%s",
        account,
        zip_filter,
        min_amount,
        max_amount,
    )

    if not account:
        # No query -> empty results
        return jsonify({"status": "success", "count": 0, "rows": []})

    try:
        row, _raw = algolia_search(account)
        if row is None:
            return jsonify({"status": "success", "count": 0, "rows": []})

        rows = [row]

        # Apply optional zip filter
        if zip_filter:
            rows = [r for r in rows if r.get("zip") == zip_filter]

        # Apply optional amount filters
        if min_amount is not None:
            rows = [r for r in rows if r.get("amountDue", 0) >= min_amount]
        if max_amount is not None:
            rows = [r for r in rows if r.get("amountDue", 0) <= max_amount]

        return jsonify({"status": "success", "count": len(rows), "rows": rows})

    except Exception as e:
        log.exception("/api/search failed")
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------
# Gunicorn entrypoint
# ---------------------------------------------------------

if __name__ == "__main__":
    # For local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
