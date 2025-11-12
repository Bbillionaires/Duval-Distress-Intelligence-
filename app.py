import os
import json
import logging
import re
from typing import Any, Dict, List

import requests
from flask import Flask, jsonify, request, send_from_directory

# -----------------------------------------------------------------------------
# Flask setup
# -----------------------------------------------------------------------------
app = Flask(__name__, static_folder=".", static_url_path="")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("distress")

# -----------------------------------------------------------------------------
# Algolia + County Taxes config
# -----------------------------------------------------------------------------
ALG_APP_ID = os.getenv("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALG_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX = os.getenv("ALG_INDEX", "fl-duval.property_tax")

ALG_ENDPOINT = f"https://{ALG_APP_ID}-dsn.algolia.net/1/indexes/{ALG_INDEX}/query"
ALG_HEADERS = {
    "X-Algolia-Application-Id": ALG_APP_ID,
    "X-Algolia-API-Key": ALG_API_KEY,
    "Content-Type": "application/json",
}

# Base domain you just confirmed
COUNTY_TAX_BASE = os.getenv("COUNTY_TAX_BASE", "https://county-taxes.net")

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
def call_algolia(query: str, hits_per_page: int = 20) -> List[Dict[str, Any]]:
    """
    Call Algolia with a simple query string and return the raw hits.
    """
    params = f"query={query}&hitsPerPage={hits_per_page}"
    payload = {"params": params}

    logger.info("Algolia query: %s", params)

    resp = requests.post(
        ALG_ENDPOINT, headers=ALG_HEADERS, data=json.dumps(payload), timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", [])
    logger.info("Algolia returned %d hits", len(hits))
    return hits


def extract_basic_fields(hit: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your Algolia records are a bit weird (nested, different key names),
    so we defensively look in several places for owner / address / zip / parcel.
    """
    # Owner name
    owner = (
        hit.get("display_name")
        or hit.get("owner_name")
        or hit.get("owner")
        or hit.get("name")
        or ""
    )

    # Address & zip
    address = ""
    zip_code = ""

    # Many records have an 'address' object
    addr_obj = hit.get("address") or hit.get("situs") or hit.get("location")
    if isinstance(addr_obj, dict):
        # e.g. {"name": "4157 LORENZO CT", "city": "JACKSONVILLE", "zip": "32208"}
        line = addr_obj.get("name") or addr_obj.get("line1") or ""
        city = addr_obj.get("city") or ""
        state = addr_obj.get("province") or addr_obj.get("state") or ""
        zip_code = addr_obj.get("zip") or addr_obj.get("postal_code") or ""
        parts = [p for p in [line, city, state, zip_code] if p]
        address = ", ".join(parts)

    # Some indexes store the full address in external_id
    if not address and isinstance(hit.get("external_id"), str):
        address = hit["external_id"]

        # Try to pull a 5-digit zip from that string
        m = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
        if m and not zip_code:
            zip_code = m.group(1)

    # Direct zip on root hit
    if not zip_code:
        zip_code = (
            hit.get("zip")
            or hit.get("postal_code")
            or (addr_obj.get("zip") if isinstance(addr_obj, dict) else "")
            or ""
        )

    # Parcel / account
    parcel = (
        hit.get("account_number")
        or hit.get("account_id")
        or hit.get("parcel")
        or hit.get("parcel_id")
        or hit.get("objectID")
        or ""
    )

    # Distress type – for now this app is only Tax distress
    distress = "Tax"

    return {
        "owner": owner,
        "address": address,
        "zip": zip_code,
        "parcel": parcel,
        "distress": distress,
        # amountDue & link will be filled later
        "amountDue": 0.0,
        "link": "",
    }


def try_parse_money_from_text(text: str) -> float:
    """
    Very loose: finds the first number that looks like money, like 12,345.67.
    Used as a fallback if the county site doesn't return JSON.
    """
    m = re.search(r"\$?\s*([0-9][0-9,]*\.?[0-9]{0,2})", text)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0


def find_amount_in_json(obj: Any) -> float:
    """
    Recursively search a JSON object for a field whose name includes 'due'
    or 'balance' and is numeric.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                amt = find_amount_in_json(v)
                if amt:
                    return amt
            else:
                if isinstance(v, (int, float)) and any(
                    key in k.lower() for key in ["due", "balance", "amount"]
                ):
                    return float(v)
    elif isinstance(obj, list):
        for item in obj:
            amt = find_amount_in_json(item)
            if amt:
                return amt
    return 0.0


def fetch_amount_due_and_link(hit: Dict[str, Any]) -> (float, str):
    """
    Uses the 'public_url' (or similar) in the Algolia hit to fetch the bill page
    from county-taxes.net and extract the total amount due.
    If anything fails, returns (0.0, '') but NEVER crashes the app.
    """
    public_url = (
        hit.get("public_url")
        or hit.get("account_url")
        or hit.get("url")
        or ""
    )

    if not public_url:
        return 0.0, ""

    if public_url.startswith("http://") or public_url.startswith("https://"):
        full_url = public_url
    else:
        # You confirmed this base domain
        full_url = COUNTY_TAX_BASE.rstrip("/") + public_url

    logger.info("Fetching bill page: %s", full_url)

    try:
        resp = requests.get(full_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch bill page: %s", e)
        return 0.0, ""

    # Try JSON first
    ct = resp.headers.get("Content-Type", "")
    if "application/json" in ct:
        try:
            data = resp.json()
            amt = find_amount_in_json(data)
            return amt, full_url
        except Exception as e:
            logger.warning("Failed to parse JSON bill: %s", e)
            return 0.0, full_url

    # Otherwise, fall back to HTML scraping – first money-ish number
    try:
        amt = try_parse_money_from_text(resp.text)
        return amt, full_url
    except Exception as e:
        logger.warning("Failed to scrape HTML bill: %s", e)
        return 0.0, full_url


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def serve_index():
    # serve index.html from project root
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def api_health():
    """
    Simple health check so Render knows the app is alive.
    """
    return jsonify({"status": "ok"})


@app.route("/api/algolia-debug")
def api_algolia_debug():
    """
    Debug endpoint: hit it like
    /api/algolia-debug?account=030147-0432

    and it will return the raw Algolia hits so you can inspect keys.
    """
    account = request.args.get("account", "").strip()
    if not account:
        return jsonify({"message": "Missing 'account' query param", "status": "error"}), 400

    try:
        hits = call_algolia(account, hits_per_page=5)
        if not hits:
            return jsonify(
                {"message": "No hits", "first_hit_keys": [], "status": "success"}
            )
        first = hits[0]
        return jsonify(
            {
                "first_hit_keys": list(first.keys()),
                "first_hit_sample": first,
                "status": "success",
            }
        )
    except Exception as e:
        logger.exception("Algolia debug failed")
        return jsonify({"message": str(e), "status": "error"}), 500


@app.route("/api/search")
def api_search():
    """
    Main distress search endpoint.

    Query params:
      - q          : account / owner / address string
      - zip        : optional zip filter
      - min_amount : optional minimum amount due
      - max_amount : optional maximum amount due
    """
    q = request.args.get("q", "").strip()
    zip_filter = request.args.get("zip", "").strip()
    min_amount_raw = request.args.get("min_amount", "").strip()
    max_amount_raw = request.args.get("max_amount", "").strip()

    def to_float(val: str) -> float:
        if not val:
            return 0.0
        try:
            return float(val)
        except ValueError:
            return 0.0

    min_amount = to_float(min_amount_raw)
    max_amount = to_float(max_amount_raw)

    if not q and not zip_filter:
        # avoid querying entire Algolia index with empty search
        return jsonify({"count": 0, "rows": [], "status": "success"})

    try:
        hits = call_algolia(q or zip_filter, hits_per_page=20)
    except Exception as e:
        logger.exception("Algolia search failed")
        return jsonify({"message": str(e), "count": 0, "rows": [], "status": "error"}), 500

    rows: List[Dict[str, Any]] = []

    for hit in hits:
        lead = extract_basic_fields(hit)

        # Fetch amount due & link (best effort)
        amount_due, link = fetch_amount_due_and_link(hit)
        lead["amountDue"] = amount_due
        lead["link"] = link

        # Zip filter
        if zip_filter and lead["zip"] and lead["zip"] != zip_filter:
            continue

        # Amount filters
        if min_amount and amount_due < min_amount:
            continue
        if max_amount and amount_due and max_amount and amount_due > max_amount:
            continue

        rows.append(lead)

    return jsonify({"count": len(rows), "rows": rows, "status": "success"})


# -----------------------------------------------------------------------------
# Main entrypoint for local dev
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # For local dev only; Render will use gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
