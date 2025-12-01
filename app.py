import os
import csv
import json
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup # currently unused, safe to keep

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

# Duval Algolia public search config
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
DUVAL_ALG_ENDPOINT = f"https://{DUVAL_ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"

# CSV & cache
CSV_PATH = os.getenv("CSV_PATH", "leads.csv")
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

# Base for Duval public site (bills page)
DUVAL_BASE_URL = "https://county-taxes.net"

# Tax certificate CSV (Duval download)
CERT_CSV_PATH = os.getenv("CERT_CSV_PATH", "duval_certificates.csv")

# ------------------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

# ------------------------------------------------------------------------------
# CSV helpers for leads.csv
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
    """Load all rows from leads.csv as list of dicts."""
    if not csv_exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_row(row: dict):
    """
    Append a row to leads.csv.

    row MUST only contain keys from CSV_FIELDS.
    """
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
# Certificate helpers (Duval tax certificate CSV)
# ------------------------------------------------------------------------------

_cert_cache = None
_cert_cache_mtime = None


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


def load_certificate_rows():
    """
    Load duval_certificates.csv into a dict keyed by parcel/account.

    We keep a small cache so we don't re-read on every request.
    """
    global _cert_cache, _cert_cache_mtime
    try:
        mtime = os.path.getmtime(CERT_CSV_PATH)
    except FileNotFoundError:
        _cert_cache = {}
        _cert_cache_mtime = None
        return _cert_cache

    # Return cache if file hasn't changed
    if _cert_cache is not None and _cert_cache_mtime == mtime:
        return _cert_cache

    with open(CERT_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or [])]

        def find_col(*candidates):
            for h in headers:
                lower = h.lower()
                for cand in candidates:
                    if cand in lower:
                        return h
            return None

        acct_col = find_col("account", "parcel")
        face_col = find_col("face amount", "face")
        months_col = find_col("avg. months", "months outstanding")

        data = {}
        for row in reader:
            if not acct_col:
                continue

            acct_raw = (row.get(acct_col) or "").strip()
            if not acct_raw:
                continue

            face_amt = normalize_amount(row.get(face_col)) if face_col else None
            months_out = normalize_amount(row.get(months_col)) if months_col else None

            years_behind = 0
            if months_out is not None:
                try:
                    years_behind = int(float(months_out) // 12)
                except Exception:
                    years_behind = 0

            key1 = acct_raw.replace(" ", "")
            key2 = key1.replace("-", "")

            meta = {
                "years_behind": years_behind,
                "unpaid_years": [], # we don't have exact years, just age
                "delinquent_total": face_amt or 0.0,
                "tax_deed_application": False, # Duval CSV doesn't expose this directly
            }
            data[key1] = meta
            data[key2] = meta

    _cert_cache = data
    _cert_cache_mtime = mtime
    return _cert_cache


def get_certificate_meta(parcel_id: str):
    """Return certificate-based meta for this parcel, if any."""
    if not parcel_id:
        return None
    certs = load_certificate_rows()
    key1 = parcel_id.replace(" ", "")
    key2 = key1.replace("-", "")
    return certs.get(key1) or certs.get(key2)


# ------------------------------------------------------------------------------
# Duval Algolia search
# ------------------------------------------------------------------------------

def search_duval_algolia(query: str, hits_per_page: int = 20):
    """
    Call Duval's public Algolia index for a query (parcel, owner, zip, etc.).
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
                "params": f"hitsPerPage={hits_per_page}&page=0&query={query}",
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
# HTML / bill parsing
# ------------------------------------------------------------------------------

def extract_amounts_from_json(data: dict):
    """
    Walk nested JSON and try to pull out totals, delinquent, last_year.
    Not heavily used, but safe to keep.
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
            if total_due is None and any(t in lk for t in ["total", "current"]):
                amt = normalize_amount(value)
                if amt is not None:
                    total_due = amt
            if delinquent_due is None and "delinquent" in lk:
                amt = normalize_amount(value)
                if amt is not None:
                    delinquent_due = amt
            if last_year_due is None and any(t in lk for t in ["prior", "last_year"]):
                amt = normalize_amount(value)
                if amt is not None:
                    last_year_due = amt
        for v in d.values():
            if isinstance(v, dict):
                search_dict(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        search_dict(item)

    search_dict(data)
    return total_due, delinquent_due, last_year_due


def fetch_duval_bill_amounts(public_url: str, debug: bool = False):
    """
    Given public_url from Algolia (like '/public_.../bills?parcel=<GUID>'),
    try JSON, HTML, then the iframe 'load-amount-due' endpoint.

    Returns (total_due, delinquent_due, last_year_due, debug_info)
    """
    import re
    import base64
    from urllib.parse import urlparse, parse_qs

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

    # Ensure leading slash
    if not public_url.startswith("/"):
        public_url = "/" + public_url

    def extract_labeled_total(html_text: str):
        """
        Look for 'TOTAL AMOUNT DUE' or 'AMOUNT DUE' near a dollar amount.
        """
        text = " ".join(html_text.split())

        # TOTAL AMOUNT DUE ... $X.XX
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

        # AMOUNT DUE ... $X.XX
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

    html_url = DUVAL_BASE_URL + public_url

    if "?" in public_url:
        json_url = DUVAL_BASE_URL + public_url + "&format=json"
    else:
        json_url = DUVAL_BASE_URL + public_url + "?format=json"

    total_due = None
    delinquent_due = None
    last_year_due = None

    # 1) JSON endpoint
    try:
        rj = requests.get(json_url, timeout=15)
        if rj.ok:
            try:
                data = rj.json()
                debug_info["json_ok"] = True
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

    # 2) HTML of main bills page
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

    # 3) Fallback iframe load-amount-due
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

    # (We still don't split delinquent vs last_year here.)
    return total_due, delinquent_due, last_year_due, debug_info


# ------------------------------------------------------------------------------
# Distress scoring
# ------------------------------------------------------------------------------

def compute_distress(total_due, delinq_meta: dict | None = None):
    """
    Decide how distressed a parcel is.

    Rules you requested:
      - High risk (level 2) if years_behind >= 2 OR delinquent_total >= 2000
      - Level 1 if any amount owed or at least 1 year behind
      - Level 3 reserved for true tax-deed application if we get that flag later.
    """
    if delinq_meta is None:
        delinq_meta = {}

    years_behind = delinq_meta.get("years_behind") or 0
    unpaid_years = delinq_meta.get("unpaid_years") or []
    tax_deed_application = bool(delinq_meta.get("tax_deed_application"))

    delinquent_total = delinq_meta.get("delinquent_total")
    if delinquent_total is None:
        try:
            delinquent_total = float(total_due) if total_due not in (None, "") else 0.0
        except (TypeError, ValueError):
            delinquent_total = 0.0

    level = 0
    desc = "No Significant Distress"

    if tax_deed_application:
        level = 3
        desc = "Tax Deed Application Filed – Auction Imminent"
    elif years_behind >= 2 or delinquent_total >= 2000:
        level = 2
        desc = "High Risk: 2+ Years Behind or ≥ $2,000 Owed"
    elif delinquent_total > 0 or years_behind >= 1:
        level = 1
        desc = "Some Amount Owed or At Least 1 Year Behind"

    is_distressed = level >= 1

    return {
        "years_behind": years_behind,
        "unpaid_years": unpaid_years,
        "delinquent_total": delinquent_total,
        "tax_deed_application": tax_deed_application,
        "distress_level": level,
        "distress_desc": desc,
        "is_distressed": is_distressed,
    }


# ------------------------------------------------------------------------------
# Routes
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


@app.route("/api/search_zip")
def search_zip():
    """
    Bulk distress search by ZIP.

    Query params:
      - zip: required (e.g. 32218)
      - min_due: optional float filter
      - max_due: optional float filter
      - debug=1: include extra scraper debug info
    """
    try:
        zip_raw = request.args.get("zip", "").strip()
        debug_flag = request.args.get("debug") == "1"

        if not zip_raw:
            return jsonify(
                {"status": "error", "message": "Missing ?zip= parameter"}
            ), 400

        # helper to parse floats
        def parse_float(val, default=None):
            if val is None or val == "":
                return default
            try:
                return float(str(val))
            except Exception:
                return default

        min_due = parse_float(request.args.get("min_due"))
        max_due = parse_float(request.args.get("max_due"))

        # Pull many hits by ZIP
        hits = search_duval_algolia(zip_raw, hits_per_page=500)
        results = []
        amount_fetch_debug = []

        for hit in hits or []:
            # Basic identity
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

            # Keep only exact ZIP matches if we know the zip
            if zip_code and zip_code != zip_raw:
                continue

            public_url = custom_params.get("public_url", "")

            # Scrape bill amounts
            total_due, delinquent_due, last_year_due, fetch_dbg = fetch_duval_bill_amounts(
                public_url
            )

            try:
                total_numeric = float(total_due) if total_due not in (None, "") else 0.0
            except (TypeError, ValueError):
                total_numeric = 0.0

            # Ignore perfectly current parcels
            if total_numeric <= 0:
                continue

            # Apply min/max filters
            if min_due is not None and total_numeric < min_due:
                continue
            if max_due is not None and total_numeric > max_due:
                continue

            # Merge in certificate meta
            cert_meta = get_certificate_meta(parcel_id)
            if cert_meta is None:
                delinq_meta = {"delinquent_total": total_numeric}
            else:
                delinq_meta = {
                    "years_behind": cert_meta.get("years_behind", 0),
                    "unpaid_years": cert_meta.get("unpaid_years", []),
                    # certificate + current due
                    "delinquent_total": (cert_meta.get("delinquent_total") or 0.0)
                    + total_numeric,
                    "tax_deed_application": cert_meta.get("tax_deed_application", False),
                }

            distress = compute_distress(total_numeric, delinq_meta)

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
                "is_distressed": distress["is_distressed"],
                # extra distress meta in API response:
                "years_behind": distress["years_behind"],
                "unpaid_years": distress["unpaid_years"],
                "delinquent_total": distress["delinquent_total"],
                "tax_deed_application": distress["tax_deed_application"],
                "distress_level": distress["distress_level"],
                "distress_desc": distress["distress_desc"],
                "source": "live_duval",
                "created_at": datetime.utcnow().isoformat(),
            }

            out_rows.append(row)

            # Only persist the subset that matches CSV_FIELDS
            row_to_save = {k: row.get(k, "") for k in CSV_FIELDS}
            save_row(row_to_save)

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


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn / local debug
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
