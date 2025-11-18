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


def extract_amounts_from_html(html: str, dbg: dict | None = None):
    """
    Very defensive HTML parser for Duval's bill page.

    Strategy:
    - Find *all* currency-looking values in the HTML.
    - Convert them to floats.
    - Heuristic: treat the *smallest positive* amount as "total_due"
      (this matches your examples where the real amount due is much
       smaller than the big tax / value numbers).
    - We leave delinquent + last_year as None for now.
    - We also push the full list of amounts into dbg["html_amounts"]
      so we can refine later if needed.
    """
    import re

    if dbg is not None:
        dbg.setdefault("html_ok", True)
        dbg["html_length"] = len(html)
        dbg["html_sample"] = html[:400]

    # Find all currency-like patterns, e.g. $1,234.56 or 104.00
    raw_amounts = re.findall(r"\$?\d[\d,]*\.\d{2}", html)
    amounts: list[float] = []

    for m in raw_amounts:
        s = m.replace("$", "").replace(",", "")
        try:
            val = float(s)
            if val > 0:
                amounts.append(val)
        except ValueError:
            continue

    if dbg is not None:
        dbg["html_amounts"] = amounts

    if not amounts:
        # Nothing found we trust
        return None, None, None

    # Heuristic:
    # - Real "amount due" on your examples is the *smallest* positive value on the page.
    # - Big numbers (millions / tens of thousands) are usually assessments, etc.
    total_due = min(amounts)

    # For now, we don't try to split delinquent vs last_year
    delinquent_due = None
    last_year_due = None

    return total_due, delinquent_due, last_year_due

def build_iframe_url_from_public(public_url: str) -> str | None:
    """
    Given Duval's public bills URL like:
      /public/real_estate/parcels/030147-0432/bills?parcel=1573c4fe-...
    build the corresponding iframe load-amount-due URL, which looks like:

      https://county-taxes.net/iframe-taxsys/duval.county-taxes.com/govhub/property-tax/
      ZHV2YWw6cmVhbF9lc3RhdGU6cGFyZW50czoxNTczYzRmZS1mYjVjLTExZWItODdkYS03ZTgwMmU0NmVlNTg=
      /load-amount-due

    The middle part is base64("duval:real_estate:parents:<parcel-guid>").
    """
    try:
        if not public_url:
            return None

        # Ensure leading slash
        if not public_url.startswith("/"):
            public_url = "/" + public_url

        parsed = urlparse(public_url)
        qs = parse_qs(parsed.query)
        guid_list = qs.get("parcel") or []
        if not guid_list:
            return None

        guid = guid_list[0]
        raw_key = f"duval:real_estate:parents:{guid}"
        encoded = base64.b64encode(raw_key.encode("utf-8")).decode("utf-8")

        return (
            f"{DUVAL_BASE_URL}"
            f"/iframe-taxsys/duval.county-taxes.com/govhub/property-tax/"
            f"{encoded}/load-amount-due"
        )
    except Exception:
        return None

def fetch_duval_bill_amounts(public_url: str, debug: bool = False):
    """
    Given public_url from Algolia (like '/public/real_estate/parcels/.../bills?parcel=<GUID>'),
    try to get the *displayed* "Total Amount Due" (or "Amount Due") for that parcel.

    This version:
      * DOES NOT guess by taking the biggest dollar amount.
      * Only uses label-based scraping from the main bills HTML page.

    Returns:
        total_due, delinquent_due, last_year_due, debug_info
    """
    import re

    debug_info = {
        "json_ok": False,
        "json_error": None,
        "html_ok": False,
        "html_error": None,
        "html_length": None,
        "html_sample": None,
    }

    if not public_url:
        return None, None, None, debug_info

    # Make sure URL starts with '/'
    if not public_url.startswith("/"):
        public_url = "/" + public_url

    html_url = DUVAL_BASE_URL + public_url

    total_due = None
    delinquent_due = None
    last_year_due = None

    try:
        rh = requests.get(html_url, timeout=15)
        if rh.ok:
            html_text = rh.text
            debug_info["html_ok"] = True
            debug_info["html_length"] = len(html_text)

            # Try to capture a sample *around* the label so we can see it in debug
            upper_html = html_text.upper()
            label_idx = upper_html.find("TOTAL AMOUNT DUE")
            if label_idx == -1:
                label_idx = upper_html.find("AMOUNT DUE")

            if label_idx != -1:
                start = max(0, label_idx - 200)
                end = min(len(html_text), label_idx + 400)
                debug_info["html_sample"] = html_text[start:end]
            else:
                # fallback sample if we never see the label at all
                debug_info["html_sample"] = html_text[:600]

            # Strip HTML tags to make pattern matching easier
            text_no_tags = re.sub(r"<[^>]+>", " ", html_text)
            text_no_tags = " ".join(text_no_tags.split())

            # 1) Look for "TOTAL AMOUNT DUE ... $X,XXX.XX"
            m = re.search(
                r"TOTAL\s+AMOUNT\s+DUE[^$]*\$(\d[\d,]*\.\d{2})",
                text_no_tags,
                re.IGNORECASE,
            )

            # 2) If that fails, look for generic "AMOUNT DUE ... $X,XXX.XX"
            if not m:
                m = re.search(
                    r"AMOUNT\s+DUE[^$]*\$(\d[\d,]*\.\d{2})",
                    text_no_tags,
                    re.IGNORECASE,
                )

            if m:
                amt_str = m.group(1).replace(",", "")
                try:
                    total_due = float(amt_str)
                except ValueError:
                    debug_info["html_error"] = f"Could not parse amount '{amt_str}'"
            else:
                debug_info["html_error"] = "Label-based search found no amount"
        else:
            debug_info["html_error"] = f"HTTP {rh.status_code}"
    except Exception as e:
        debug_info["html_error"] = f"request error: {e}"

    # delinquent_due / last_year_due still None until we decide how to parse them
    return total_due, delinquent_due, last_year_due, debug_info
    
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

    - Normalizes the parcel (with and without dash).
    - Checks CSV cache for this parcel (<= CACHE_DAYS old).
    - If found, returns cached rows.
    - Else:
        * hits Duval Algolia for that parcel (no local data needed),
        * for each hit: fetches bill amounts (HTML scraper),
        * stores a compact row in CSV (no duplicates by parcel),
        * returns the fresh rows.

    If ?debug=1 is in the query string, we also return a "debug" block
    showing parcel normalization and amount-fetch info.
    """
    parcel_raw = request.args.get("parcel", "").strip()
    debug_flag = request.args.get("debug") == "1"

    if not parcel_raw:
        return jsonify(
            {"status": "error", "message": "Missing ?parcel= parameter"}
        ), 400

    # Normalize parcel: remove dash, then re-add to standard format if length 10
    parcel_no_dash = parcel_raw.replace("-", "")
    parcel_dashed = parcel_raw
    if len(parcel_no_dash) == 10:
        parcel_dashed = f"{parcel_no_dash[:-4]}-{parcel_no_dash[-4:]}"

    # ------------------------------------------------------------------
    # 1) CSV cache first (we store parcel as dashed form)
    cached_rows = find_recent_csv_rows(parcel_dashed)

    all_missing_amounts = False
    if cached_rows:
        # True if *every* cached row is missing all amounts
        all_missing_amounts = all(
            (row.get("total_due") in (None, ""))
            and (row.get("delinquent_due") in (None, ""))
            and (row.get("last_year_due") in (None, ""))
            for row in cached_rows
        )

    # Only use cache if there is data AND at least one row has some amount info
    if cached_rows and not all_missing_amounts:
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

    # ------------------------------------------------------------------
    # 2) Live Algolia lookup (Duval)
    #    We'll try both "no dash" and "dashed" forms to be safe.
    # ------------------------------------------------------------------
    hits = search_duval_algolia(parcel_no_dash)
    if not hits:
        hits = search_duval_algolia(parcel_dashed)

    if not hits:
        response = {
            "status": "success",
            "source": "none",
            "count": 0,
            "rows": [],
        }
        if debug_flag:
            response["debug"] = {
                "parcel_raw": parcel_raw,
                "parcel_no_dash": parcel_no_dash,
                "parcel_dashed": parcel_dashed,
                "hits_found": 0,
            }
        return jsonify(response)

    out_rows = []
    amount_fetch_debug = []

    for hit in hits:
        # -----------------------------
        # Basic identity fields
        # -----------------------------
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

        # -----------------------------
        # Fetch bill amounts via Duval
        # -----------------------------
        total_due, delinquent_due, last_year_due, fetch_dbg = fetch_duval_bill_amounts(
            public_url
        )

        # For CSV, store empty string if the amount is None
        row = {
            "parcel": parcel_id,
            "owner_name": owner_name,
            "display_name": display_name,
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "public_url": public_url,
            "total_due": total_due if total_due is not None else "",
            "delinquent_due": delinquent_due if delinquent_due is not None else "",
            "last_year_due": last_year_due if last_year_due is not None else "",
            "source": "live_duval",
            "created_at": datetime.utcnow().isoformat(),
        }

        # Avoid duplicates within this response
        if not any(r["parcel"] == row["parcel"] for r in out_rows):
            out_rows.append(row)
            save_row(row)

        # Collect fetch-debug info per parcel if we are in debug mode
        amount_fetch_debug.append(
            {
                "parcel": parcel_id,
                "public_url": public_url,
                "amounts": {
                    "total_due": total_due,
                    "delinquent_due": delinquent_due,
                    "last_year_due": last_year_due,
                },
                "fetch_debug": fetch_dbg,
            }
        )

    # -----------------------------
    # Build final JSON response
    # -----------------------------
    response = {
        "status": "success",
        "source": "live_duval",
        "count": len(out_rows),
        "rows": out_rows,
    }

    if debug_flag:
        response["debug"] = {
            "parcel_raw": parcel_raw,
            "parcel_no_dash": parcel_no_dash,
            "parcel_dashed": parcel_dashed,
            "hits_found": len(hits),
            "amount_fetch": amount_fetch_debug,
        }

    return jsonify(response)


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
