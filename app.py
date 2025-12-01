import os
import csv
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

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
    "total_due_numeric",
    "is_distressed",
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
# Bill amount fetching
# ------------------------------------------------------------------------------

def fetch_duval_bill_amounts(public_url: str, debug: bool = False):
    """
    Given public_url from Algolia (like '/public_.../bills?parcel=<GUID>'),
    try several strategies to get the **Total Amount Due**:

      1) JSON endpoint (bills?format=json) – usually empty, but harmless to try.
      2) HTML of the main bills page.
      3) Fallback: the iframe 'load-amount-due' endpoint that the UI uses.

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
        "load_ok": False,
        "load_error": None,
        "load_html_length": None,
        "load_html_sample": None,
    }

    if not public_url:
        return None, None, None, debug_info

    # Make sure URL starts with '/'
    if not public_url.startswith("/"):
        public_url = "/" + public_url

    # ------------ Helper: find "TOTAL AMOUNT DUE ... $X.XX" in HTML ------------
    def extract_labeled_total(html_text: str):
        """
        Look for labels like 'TOTAL AMOUNT DUE' or 'AMOUNT DUE' followed
        shortly by a dollar amount. This avoids grabbing random larger numbers
        elsewhere on the page (certificates, face amounts, etc.).
        """
        text = " ".join(html_text.split())

        # Pattern 1: "TOTAL AMOUNT DUE ... $X.XX"
        m = re.search(
            r"TOTAL\s+AMOUNT\s+DUE[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
            text,
            re.IGNORECASE,
        )
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        # Pattern 2: fallback "AMOUNT DUE ... $X.XX"
        m = re.search(
            r"AMOUNT\s+DUE[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
            text,
            re.IGNORECASE,
        )
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        return None

    # Build URLs we will try
    html_url = DUVAL_BASE_URL + public_url

    if "?" in public_url:
        json_url = DUVAL_BASE_URL + public_url + "&format=json"
    else:
        json_url = DUVAL_BASE_URL + public_url + "?format=json"

    total_due = None
    delinquent_due = None
    last_year_due = None

    # ------------------------- 1) Try JSON endpoint ---------------------------
    try:
        rj = requests.get(json_url, timeout=15)
        if rj.ok:
            try:
                data = rj.json()
                debug_info["json_ok"] = True
                # If they ever expose totals here, use them.
                for key in ["total_due", "amount_due", "totalAmountDue"]:
                    if key in data:
                        try:
                            total_due = float(
                                str(data[key]).replace("$", "").replace(",", "")
                            )
                            break
                        except ValueError:
                            pass
            except Exception as e:
                debug_info["json_error"] = str(e)
        else:
            debug_info["json_error"] = f"HTTP {rj.status_code}"
    except Exception as e:
        debug_info["json_error"] = str(e)

    # -------------------- 2) Try HTML of the main bills page ------------------
    if total_due is None:
        try:
            rh = requests.get(html_url, timeout=15)
            if rh.ok:
                debug_info["html_ok"] = True
                debug_info["html_length"] = len(rh.text)
                debug_info["html_sample"] = rh.text[:400]

                amt = extract_labeled_total(rh.text)
                if amt is not None:
                    total_due = amt
            else:
                debug_info["html_error"] = f"HTTP {rh.status_code}"
        except Exception as e:
            debug_info["html_error"] = str(e)

    # ------------------ 3) Fallback: iframe load-amount-due -------------------
    if total_due is None:
        try:
            parsed = urlparse(public_url)
            qs = parse_qs(parsed.query)
            guid = qs.get("parcel", [None])[0]

            if guid:
                token_str = f"duval:real_estate:parents:{guid}"
                token_b64 = base64.b64encode(token_str.encode("utf-8")).decode("utf-8")

                load_url = (
                    "https://county-taxes.net/iframe-taxsys/duval.county-taxes.com/"
                    f"govhub/property-tax/{token_b64}/load-amount-due"
                )

                rl = requests.get(load_url, timeout=15)
                if rl.ok:
                    debug_info["load_ok"] = True
                    debug_info["load_html_length"] = len(rl.text)
                    debug_info["load_html_sample"] = rl.text[:400]

                    amt = extract_labeled_total(rl.text)
                    if amt is not None:
                        total_due = amt
                else:
                    debug_info["load_error"] = f"HTTP {rl.status_code}"
            else:
                debug_info["load_error"] = "No GUID in public_url query"
        except Exception as e:
            debug_info["load_error"] = str(e)

    return total_due, delinquent_due, last_year_due, debug_info


# ------------------------------------------------------------------------------
# API Routes
# ------------------------------------------------------------------------------

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
    try:
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

            # Normalize total_due into a numeric value and mark distressed status
            try:
                total_numeric = float(total_due) if total_due not in (None, "") else 0.0
            except (TypeError, ValueError):
                total_numeric = 0.0

            # Example rule: distressed if they owe more than $0
            is_distressed = total_numeric > 0

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
                "total_due_numeric": total_numeric,
                "is_distressed": is_distressed,
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

    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "message": "Unhandled exception in /api/parcel",
                "error": str(e),
            }
        ), 500


@app.route("/api/search_zip")
def search_zip():
    """
    Bulk distress search by ZIP.

    Query params:
      - zip: required (e.g. 32209)
      - min_due: optional, float (filter out properties that owe less)
      - max_due: optional, float (filter out properties that owe more)
      - debug: optional, "1" to include debug info

    It:
      * calls Algolia using the ZIP as the search query,
      * filters hits whose entity zip matches exactly,
      * fetches live bill amounts for each, like /api/parcel,
      * returns many rows in the same shape as parcel_lookup.
    """
    try:
        zip_raw = request.args.get("zip", "").strip()
        debug_flag = request.args.get("debug") == "1"

        if not zip_raw:
            return jsonify({"status": "error", "message": "Missing ?zip= parameter"}), 400

        # Helper to parse optional floats
        def parse_float(val, default=None):
            if val is None or val == "":
                return default
            try:
                return float(str(val))
            except Exception:
                return default

        min_due = parse_float(request.args.get("min_due"))
        max_due = parse_float(request.args.get("max_due"))

        # 1) Search Algolia using the ZIP as the query
        hits = search_duval_algolia(zip_raw)
        results = []
        amount_fetch_debug = []

        for hit in hits or []:
            # -----------------------------
            # Basic identity fields (same style as parcel_lookup)
            # -----------------------------
            parcel_id = (
                hit.get("external_id")
                or hit.get("parcel")
                or hit.get("objectID")
            )

            owner_name = ""
            display_name = hit.get("display_name") or ""
            address = ""
            city = ""
            state = ""
            zip_code = ""

            custom_params = hit.get("custom_parameters") or {}
            entities = custom_params.get("entities") or []
            if isinstance(entities, list) and entities:
                first = entities[0]
                owner_name = first.get("name", "") or display_name
                address = first.get("address", "")
                city = first.get("city", "")
                state = first.get("state", "")
                zip_code = first.get("zip", "")

            # Only keep exact ZIP matches if we have a zip_code
            if zip_code and zip_code != zip_raw:
                continue

            public_url = custom_params.get("public_url", "")

            # -----------------------------
            # Fetch bill amounts like parcel_lookup
            # -----------------------------
            total_due, delinquent_due, last_year_due, fetch_dbg = fetch_duval_bill_amounts(
                public_url
            )

            # Normalize to numeric for filtering
            try:
                total_numeric = float(total_due) if total_due not in (None, "") else 0.0
            except (TypeError, ValueError):
                total_numeric = 0.0

            # Apply min / max filters
            if min_due is not None and total_numeric < min_due:
                continue
            if max_due is not None and total_numeric > max_due:
                continue

            is_distressed = total_numeric > 0

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
                "total_due_numeric": total_numeric,
                "is_distressed": is_distressed,
                "source": "live_duval_zip",
                "created_at": datetime.utcnow().isoformat(),
            }

            results.append(row)
            save_row(row)

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

        resp = {
            "status": "success",
            "source": "live_duval_zip",
            "count": len(results),
            "rows": results,
        }

        if debug_flag:
            resp["debug"] = {
                "zip": zip_raw,
                "hits_found": len(hits or []),
                "amount_fetch": amount_fetch_debug,
            }

        return jsonify(resp)

    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "message": "Unhandled exception in /api/search_zip",
                "error": str(e),
            }
        ), 500


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
