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

# Duval Algolia public search config (this is what the browser uses)
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
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

def search_duval_algolia(parcel: str):
    """
    Call Duval's public Algolia index for a parcel / external_id.
    """
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
            {
                "indexName": DUVAL_ALG_INDEX,
                "params": f"hitsPerPage=20&query={parcel}",
            }
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
        if not results:
            return []
        return results[0].get("hits", [])
    except Exception:
        return []


# ------------------------------------------------------------------------------
# Amount parsing helpers
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
    Walks nested dicts/lists and looks for keys that smell like totals,
    delinquent, prior year, etc.
    """
    total_due = None
    delinquent_due = None
    last_year_due = None

    def search_dict(d):
        nonlocal total_due, delinquent_due, last_year_due
        if not isinstance(d, dict):
            return

        for key, value in d.items():
            lk = key.lower()

            # total / current
            if total_due is None and any(t in lk for t in ["total", "current"]):
                amt = normalize_amount(value)
                if amt is not None:
                    total_due = amt

            # delinquent
            if delinquent_due is None and "delinquent" in lk:
                amt = normalize_amount(value)
                if amt is not None:
                    delinquent_due = amt

            # prior / last year
            if last_year_due is None and any(t in lk for t in ["prior", "last_year"]):
                amt = normalize_amount(value)
                if amt is not None:
                    last_year_due = amt

        # Dive deeper into nested dicts/lists
        for v in d.values():
            if isinstance(v, dict):
                search_dict(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        search_dict(item)

    search_dict(data)
    return total_due, delinquent_due, last_year_due


def extract_amounts_from_html(html: str):
    """
    Parse the Duval bills HTML (from load-amount-due) and grab the Total Amount Due.

    Duval often puts the amount inside hidden inputs or attributes, not just visible
    text, so we scan the entire raw HTML string (not only soup.stripped_strings).

    Strategy:
      1) Run regex over the full HTML to find $X,XXX.XX patterns.
      2) If none, look for plain X,XXX.XX patterns.
      3) Convert all matches to floats.
      4) Return the *largest* one as total_due.
    """
    import re

    # Work on the raw HTML so we catch values in attributes like value="3323.14"
    page = html

    # Matches: $3,323.14 or $104.25
    dollar_pattern = re.compile(r"\$\s*\d[\d,]*\.\d{2}")
    # Matches: 3,323.14 or 104.25 (no dollar sign)
    plain_pattern = re.compile(r"\b\d[\d,]*\.\d{2}\b")

    matches = dollar_pattern.findall(page)

    if not matches:
        # If we didn't see a $ sign, fall back to plain numbers
        matches = plain_pattern.findall(page)

    amounts = []
    for m in matches:
        val = normalize_amount(m)
        if val is not None and val >= 0.01:
            amounts.append(val)

    if not amounts:
        # Couldn’t find anything that looks like money
        return None, None, None

    # Heuristic: on these pages the largest numeric amount is the Total Amount Due
    total_due = max(amounts)

    # We’re not scrapping delinquent / last-year separately yet
    delinquent_due = None
    last_year_due = None

    return total_due, delinquent_due, last_year_due


def fetch_duval_bill_amounts(public_url: str):
    """
    Given public_url from Algolia (like '/public/real_estate/parcels/.../bills?...'),
    try:
      1) JSON endpoint (by adding &format=json or ?format=json),
      2) fallback to HTML parse via extract_amounts_from_html.

    Returns:
      (total_due, delinquent_due, last_year_due, fetch_debug)
    """
    if not public_url:
        return None, None, None, {
            "json_ok": False,
            "json_error": "no_public_url",
            "html_ok": False,
            "html_error": "no_public_url",
            "html_length": 0,
            "html_sample": "",
        }

    if not public_url.startswith("/"):
        public_url = "/" + public_url

    html_url = DUVAL_BASE_URL + public_url

    # If a query string exists, append &format=json, otherwise ?format=json
    if "?" in public_url:
        json_url = DUVAL_BASE_URL + public_url + "&format=json"
    else:
        json_url = DUVAL_BASE_URL + public_url + "?format=json"

    total_due = None
    delinquent_due = None
    last_year_due = None

    fetch_debug = {
        "json_ok": False,
        "json_error": None,
        "html_ok": False,
        "html_error": None,
        "html_length": 0,
        "html_sample": "",
    }

    # 1) Try JSON
    try:
        rj = requests.get(json_url, timeout=15)
        if rj.ok:
            fetch_debug["json_ok"] = True
            try:
                data = rj.json()
                t, d, l = extract_amounts_from_json(data)
                total_due = t
                delinquent_due = d
                last_year_due = l
            except Exception as e:
                fetch_debug["json_error"] = str(e)
        else:
            fetch_debug["json_error"] = f"status_{rj.status_code}"
    except Exception as e:
        fetch_debug["json_error"] = str(e)

    # 2) If still nothing, try HTML
    if total_due is None and delinquent_due is None and last_year_due is None:
        try:
            rh = requests.get(html_url, timeout=15)
            fetch_debug["html_ok"] = rh.ok
            if rh.ok:
                html_text = rh.text
                fetch_debug["html_length"] = len(html_text)
                # capture a small slice so we can see what the backend actually sees
                fetch_debug["html_sample"] = html_text[:400]

                t, d, l = extract_amounts_from_html(html_text)
                total_due = t
                delinquent_due = d
                last_year_due = l
            else:
                fetch_debug["html_error"] = f"status_{rh.status_code}"
        except Exception as e:
            fetch_debug["html_error"] = str(e)

    return total_due, delinquent_due, last_year_due, fetch_debug

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
    parcel_raw = request.args.get("parcel", "").strip()
    debug_flag = request.args.get("debug", "0") == "1"

    if not parcel_raw:
        return jsonify(
            {"status": "error", "message": "Missing ?parcel= parameter"}
        ), 400

    # Normalize parcel formats we will try: no dash and with dash
    parcel_no_dash = parcel_raw.replace("-", "")
    parcel_dashed = (
        f"{parcel_no_dash[:-4]}-{parcel_no_dash[-4:]}"
        if len(parcel_no_dash) > 4
        else parcel_raw
    )

    # 1) CSV cache first (we store parcel as dashed form)
    cached_rows = find_recent_csv_rows(parcel_dashed)
    if cached_rows:
        resp = {
            "status": "success",
            "source": "csv",
            "count": len(cached_rows),
            "rows": cached_rows,
        }
        if debug_flag:
            resp["debug"] = {
                "parcel_raw": parcel_raw,
                "parcel_no_dash": parcel_no_dash,
                "parcel_dashed": parcel_dashed,
            }
        return jsonify(resp)

    # 2) Live Algolia lookup (Duval), try both formats
    hits = search_duval_algolia(parcel_no_dash)
    if not hits:
        hits = search_duval_algolia(parcel_dashed)

    if not hits:
        resp = {
            "status": "success",
            "source": "none",
            "count": 0,
            "rows": [],
        }
        if debug_flag:
            resp["debug"] = {
                "parcel_raw": parcel_raw,
                "parcel_no_dash": parcel_no_dash,
                "parcel_dashed": parcel_dashed,
                "hits_found": 0,
            }
        return jsonify(resp)

    out_rows = []
    debug_amounts = []

    for hit in hits:
        # Basic identity fields
        parcel_id = (
            hit.get("external_id")
            or hit.get("parcel")
            or hit.get("objectID")
            or parcel_dashed
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
        total_due, delinquent_due, last_year_due, dbg = fetch_duval_bill_amounts(
            public_url, debug=debug_flag
        )
        if debug_flag:
            debug_amounts.append(
                {
                    "parcel": parcel_id,
                    "public_url": public_url,
                    "amounts": {
                        "total_due": total_due,
                        "delinquent_due": delinquent_due,
                        "last_year_due": last_year_due,
                    },
                    "fetch_debug": dbg,
                }
            )

        row = {
            "parcel": parcel_id,
            "owner_name": owner_name,
            "display_name": display_name,
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "public_url": public_url,
            "total_due": "" if total_due is None else total_due,
            "delinquent_due": "" if delinquent_due is None else delinquent_due,
            "last_year_due": "" if last_year_due is None else last_year_due,
            "source": "live_duval",
            "created_at": datetime.utcnow().isoformat(),
        }

        # Avoid duplicates within this response
        if not any(r["parcel"] == row["parcel"] for r in out_rows):
            out_rows.append(row)
            save_row(row)

    resp = {
        "status": "success",
        "source": "live_duval",
        "count": len(out_rows),
        "rows": out_rows,
    }
    if debug_flag:
        resp["debug"] = {
            "parcel_raw": parcel_raw,
            "parcel_no_dash": parcel_no_dash,
            "parcel_dashed": parcel_dashed,
            "hits_found": len(hits),
            "amount_fetch": debug_amounts,
        }
    return jsonify(resp)


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
