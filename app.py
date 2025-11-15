import os
import csv
import json
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

# Duval Algolia public search config (this is what your browser uses)
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"

# IMPORTANT: this matches the browser's network call with "requests":[...]
DUVAL_ALG_ENDPOINT = (
    f"https://{DUVAL_ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"
)

# CSV & cache
CSV_PATH = os.getenv("CSV_PATH", "leads.csv")
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

# Base for Duval public site (bills page)
DUVAL_BASE_URL = "https://county-taxes.net"


# ------------------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)


# ------------------------------------------------------------------------------
# CSV helpers
# ------------------------------------------------------------------------------

CSV_FIELDS = [
    "parcel",
    "owner_name",
    "display_name",
    "address",
    "city",
    "state",
    "zip",
    "public_url",
    "total_due",
    "delinquent_due",
    "last_year_due",
    "source",
    "created_at",
]


def csv_exists() -> bool:
    return os.path.exists(CSV_PATH)


def load_csv_rows():
    """Load all rows from CSV as a list of dicts."""
    if not csv_exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_row(row: dict):
    """Append a row to CSV, writing header if file doesn't exist."""
    file_exists = csv_exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def find_recent_csv_rows(parcel: str):
    """Return CSV rows for this parcel that are newer than CACHE_DAYS."""
    if not csv_exists():
        return []

    cutoff = datetime.utcnow() - timedelta(days=CACHE_DAYS)
    rows = []
    for row in load_csv_rows():
        if row.get("parcel") != parcel:
            continue
        created_str = row.get("created_at")
        if not created_str:
            continue
        try:
            created_at = datetime.fromisoformat(created_str)
        except Exception:
            continue
        if created_at >= cutoff:
            rows.append(row)
    return rows


# ------------------------------------------------------------------------------
# Duval Algolia search (live, no local data needed)
# ------------------------------------------------------------------------------

def _normalize_parcel_variants(parcel: str):
    """
    Given '0301470432' or '030147-0432', return:
      raw, no_dash, dashed
    e.g. '0301470432' -> ('0301470432', '0301470432', '030147-0432')
    """
    raw = parcel.strip()
    no_dash = raw.replace("-", "")
    dashed = raw
    # Duval style appears to be 6 digits + '-' + 4 digits
    if "-" not in dashed and len(no_dash) == 10:
        dashed = f"{no_dash[:-4]}-{no_dash[-4:]}"
    return raw, no_dash, dashed


def search_duval_algolia(parcel: str):
    """
    Call Duval's public Algolia index for a parcel / external_id.

    We try several strategies in one multi-query call:
      1) query = dashed parcel
      2) filters on external_id
      3) filters on external_id_tokens (no-dash)
    """
    raw, no_dash, dashed = _normalize_parcel_variants(parcel)

    headers = {
        "x-algolia-application-id": DUVAL_ALG_APP_ID,
        "x-algolia-api-key": DUVAL_ALG_API_KEY,
        "x-algolia-agent": (
            "Algolia for JavaScript (4.23.3); Browser (lite); "
            "instantsearch.js (4.66.1); Vue (3.3.4); Vue InstantSearch (4.15.0); "
            "JS Helper (3.17.0)"
        ),
        "Content-Type": "application/json",
    }

    body = {
        "requests": [
            # 1) The simple search the UI does
            {
                "indexName": DUVAL_ALG_INDEX,
                "params": f"hitsPerPage=20&query={dashed}",
            },
            # 2) Be explicit on external_id (e.g. "030147-0432")
            {
                "indexName": DUVAL_ALG_INDEX,
                "params": f'hitsPerPage=20&filters=external_id:"{dashed}"',
            },
            # 3) Match their external_id_tokens (e.g. "0301470432")
            {
                "indexName": DUVAL_ALG_INDEX,
                "params": f'hitsPerPage=20&filters=external_id_tokens:"{no_dash}"',
            },
        ]
    }

    try:
        resp = requests.post(
            DUVAL_ALG_ENDPOINT,
            headers=headers,
            data=json.dumps(body),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []

        # Merge hits from all 3 strategies, dedupe by objectID/external_id
        hits_by_id = {}
        for block in results:
            for hit in block.get("hits", []):
                key = hit.get("objectID") or hit.get("external_id")
                if key and key not in hits_by_id:
                    hits_by_id[key] = hit

        return list(hits_by_id.values())
    except Exception:
        # If Algolia fails for any reason, act like "no hits" instead of crashing
        return []


# ------------------------------------------------------------------------------
# Duval bill details: JSON + HTML scraper
# ------------------------------------------------------------------------------

def normalize_amount(val):
    """
    Turn strings like '$1,234.56' into float 1234.56.
    Returns None if it can't parse.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_amounts_from_json(data: dict):
    """
    Try to pull useful amount fields from a JSON response.
    We don't know exact schema, so we guess common patterns and also
    look under a 'bill' or 'bills' object.
    """
    total_due = None
    delinquent_due = None
    last_year_due = None

    candidates_total = [
        "total_due",
        "amount_due",
        "current_due",
        "totalAmountDue",
    ]
    candidates_delinquent = [
        "delinquent_due",
        "delinquentAmount",
        "delinquent_due_total",
    ]
    candidates_last_year = [
        "prior_year_due",
        "last_year_due",
        "priorYearAmount",
    ]

    # Check top-level keys
    for key in candidates_total:
        if key in data:
            total_due = normalize_amount(data[key])
            break

    for key in candidates_delinquent:
        if key in data:
            delinquent_due = normalize_amount(data[key])
            break

    for key in candidates_last_year:
        if key in data:
            last_year_due = normalize_amount(data[key])
            break

    # Check nested 'bill' or 'bills'
    bills = []
    if isinstance(data, dict):
        if isinstance(data.get("bill"), dict):
            bills = [data["bill"]]
        elif isinstance(data.get("bills"), list):
            bills = data["bills"]

    for bill in bills:
        if total_due is None:
            for key in candidates_total:
                if key in bill:
                    total_due = normalize_amount(bill[key])
                    break
        if delinquent_due is None:
            for key in candidates_delinquent:
                if key in bill:
                    delinquent_due = normalize_amount(bill[key])
                    break
        if last_year_due is None:
            for key in candidates_last_year:
                if key in bill:
                    last_year_due = normalize_amount(bill[key])
                    break

    return total_due, delinquent_due, last_year_due


def extract_amounts_from_html(html: str):
    """
    Fallback: parse the bills page HTML and look for common labels
    like 'TOTAL AMOUNT DUE', 'DELINQUENT', 'PRIOR YEAR'.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings).upper()

    total_due = None
    delinquent_due = None
    last_year_due = None

    def find_amount_near(label):
        idx = text.find(label)
        if idx == -1:
            return None
        snippet = text[idx: idx + 160]
        import re

        m = re.search(r"\$?\d[\d,]*\.?\d*", snippet)
        if m:
            return normalize_amount(m.group(0))
        return None

    # Try to grab amounts near common labels
    if total_due is None:
        total_due = find_amount_near("TOTAL AMOUNT DUE")
    if total_due is None:
        total_due = find_amount_near("TOTAL DUE")

    if delinquent_due is None:
        delinquent_due = find_amount_near("DELINQUENT")

    if last_year_due is None:
        last_year_due = find_amount_near("PRIOR YEAR")

    return total_due, delinquent_due, last_year_due


def fetch_duval_bill_amounts(public_url: str):
    """
    Given public_url from Algolia (like '/public/real_estate/parcels/.../bills?...'),
    try:
      1) JSON endpoint (by adding &format=json or ?format=json),
      2) fallback to HTML parse.
    Returns (total_due, delinquent_due, last_year_due) as floats or None.
    """
    if not public_url:
        return None, None, None

    if not public_url.startswith("/"):
        public_url = "/" + public_url

    html_url = DUVAL_BASE_URL + public_url

    # If a query string exists, append &format=json, otherwise ?format=json
    if "?" in public_url:
        json_url = DUVAL_BASE_URL + public_url + "&format=json"
    else:
        json_url = DUVAL_BASE_URL + public_url + "?format=json"

    total_due = delinquent_due = last_year_due = None

    # 1) Try JSON
    try:
        rj = requests.get(json_url, timeout=15)
        if rj.ok:
            data = rj.json()
            total_due, delinquent_due, last_year_due = extract_amounts_from_json(data)
    except Exception:
        pass

    # 2) If still nothing, try HTML
    if total_due is None and delinquent_due is None and last_year_due is None:
        try:
            rh = requests.get(html_url, timeout=15)
            if rh.ok:
                total_due, delinquent_due, last_year_due = extract_amounts_from_html(
                    rh.text
                )
        except Exception:
            pass

    return total_due, delinquent_due, last_year_due


# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.route("/")
def root():
    return "Duval Distress Intelligence backend is online (Duval Algolia + CSV cache + amounts)."


@app.route("/api/health")
def health():
    info = {
        "status": "ok",
        "duval_alg_app_id": DUVAL_ALG_APP_ID,
        "duval_alg_index": DUVAL_ALG_INDEX,
        "using_duval_algolia": True,
        "csv_path": CSV_PATH,
        "csv_exists": csv_exists(),
        "csv_size": len(load_csv_rows()) if csv_exists() else 0,
        "cache_days": CACHE_DAYS,
    }
    return jsonify(info)


@app.route("/api/parcel")
def parcel_lookup():
    """
    Main endpoint:

    - checks CSV cache for this parcel (<= CACHE_DAYS old)
    - if found, returns cached row(s)
    - else:
        * hits Duval Algolia for that parcel
        * for each hit: fetches bill amounts (JSON+HTML scrape)
        * stores a compact row in CSV (no duplicates by parcel)
        * returns the fresh rows
    """
    parcel = request.args.get("parcel", "").strip()
    if not parcel:
        return jsonify(
            {"status": "error", "message": "Missing ?parcel= parameter"}
        ), 400

    raw, no_dash, dashed = _normalize_parcel_variants(parcel)

    # 1) CSV cache first
    cached_rows = find_recent_csv_rows(parcel)
    if cached_rows:
        return jsonify(
            {
                "status": "success",
                "source": "csv",
                "count": len(cached_rows),
                "rows": cached_rows,
                "debug": {
                    "parcel_raw": raw,
                    "parcel_no_dash": no_dash,
                    "parcel_dashed": dashed,
                },
            }
        )

    # 2) Live Algolia lookup (Duval)
    hits = search_duval_algolia(parcel)
    if not hits:
        return jsonify(
            {
                "status": "success",
                "source": "none",
                "count": 0,
                "rows": [],
                "debug": {
                    "parcel_raw": raw,
                    "parcel_no_dash": no_dash,
                    "parcel_dashed": dashed,
                },
            }
        )

    out_rows = []

    for hit in hits:
        # Basic identity fields
        parcel_id = (
            hit.get("external_id")
            or hit.get("parcel")
            or hit.get("objectID")
            or parcel
        )

        owner_name = ""
        display_name = hit.get("display_name") or ""
        address = ""
        city = ""
        state = ""
        zip_code = ""

        # Try to pull address info from custom_parameters.entities
        custom_params = hit.get("custom_parameters") or {}
        entities = custom_params.get("entities") or []
        if isinstance(entities, list) and entities:
            first = entities[0]
            owner_name = first.get("name", "") or display_name
            address = first.get("address", "")
            city = first.get("city", "")
            state = first.get("state", "")
            zip_code = first.get("zip", "")

        public_url = custom_params.get("public_url", "")

        # 3) Fetch bill amounts via Duval API / HTML
        total_due, delinquent_due, last_year_due = fetch_duval_bill_amounts(public_url)

        row = {
            "parcel": parcel_id,
            "owner_name": owner_name,
            "display_name": display_name,
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "public_url": public_url,
            "total_due": total_due,
            "delinquent_due": delinquent_due,
            "last_year_due": last_year_due,
            "source": "live_duval",
            "created_at": datetime.utcnow().isoformat(),
        }

        # Avoid duplicates within this response
        if not any(r["parcel"] == row["parcel"] for r in out_rows):
            out_rows.append(row)
            save_row(row)

    return jsonify(
        {
            "status": "success",
            "source": "live_duval",
            "count": len(out_rows),
            "rows": out_rows,
            "debug": {
                "parcel_raw": raw,
                "parcel_no_dash": no_dash,
                "parcel_dashed": dashed,
            },
        }
    )


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
