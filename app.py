import os
import csv
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

# ------------------------------------------------------------------------------
# Duval county Algolia config (their public search endpoint)
# ------------------------------------------------------------------------------

# These are the values you captured from network inspector on county-taxes.net
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
DUVAL_ALG_HOST = f"{DUVAL_ALG_APP_ID}-dsn.algolia.net"

# ------------------------------------------------------------------------------
# Local cache config
# ------------------------------------------------------------------------------

CSV_PATH = os.getenv("CSV_PATH", "leads.csv")
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

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


def save_lead_from_hit(parcel: str, hit: dict, source: str):
    """
    Save a single hit into CSV (if not already present by parcel).
    We only keep a subset of fields so CSV stays simple.
    """
    if not parcel:
        return

    # Avoid duplicates by parcel
    rows = load_csv_rows()
    for row in rows:
        if row.get("parcel") == parcel:
            return  # already stored

    row = {
        "parcel": parcel,
        "owner_name": hit.get("owner_name", "") or hit.get("owner", ""),
        "display_name": hit.get("display_name", ""),
        "address": hit.get("address", ""),
        "city": hit.get("city", ""),
        "state": hit.get("state", ""),
        "zip": hit.get("zip", "") or hit.get("zip_code", ""),
        "source": source,
        "created_at": datetime.utcnow().isoformat(),
    }

    file_exists = csv_exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def find_recent_csv_rows(parcel: str):
    """
    Return CSV rows for this parcel that are newer than CACHE_DAYS.
    """
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
# Direct call to Duval county Algolia
# ------------------------------------------------------------------------------

def search_duval_algolia_by_parcel(parcel: str):
    """
    Call the SAME Algolia that county-taxes.net uses.
    We mimic their 'query = parcel number' behavior.
    """
    url = f"https://{DUVAL_ALG_HOST}/1/indexes/{DUVAL_ALG_INDEX}/query"

    headers = {
        "X-Algolia-Application-Id": DUVAL_ALG_APP_ID,
        "X-Algolia-API-Key": DUVAL_ALG_API_KEY,
        "Content-Type": "application/json",
    }

    # This matches what you saw in devtools: query=PARCEL
    body = {
        "params": f"query={parcel}&hitsPerPage=20"
    }

    resp = requests.post(url, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", [])
    return hits


# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.route("/")
def root():
    return "Duval Distress Intelligence backend is online (Duval Algolia + CSV cache)."


@app.route("/api/health")
def health():
    info = {
        "status": "ok",
        "using_duval_algolia": True,
        "duval_alg_app_id": DUVAL_ALG_APP_ID,
        "duval_alg_index": DUVAL_ALG_INDEX,
        "cache_days": CACHE_DAYS,
        "csv_path": CSV_PATH,
        "csv_exists": csv_exists(),
        "csv_size": len(load_csv_rows()) if csv_exists() else 0,
    }
    return jsonify(info)


@app.route("/api/parcel")
def parcel_lookup():
    """
    ?parcel=0862860000  or  ?parcel=030147-0432

    1) Check CSV cache (fresh within CACHE_DAYS)
    2) If none, call Duval Algolia live
    3) Save hits into CSV and return to caller
    """
    parcel = request.args.get("parcel", "").strip()
    if not parcel:
        return jsonify(
            {"status": "error", "message": "Missing ?parcel= parameter"}
        ), 400

    # 1) CSV cache
    cached_rows = find_recent_csv_rows(parcel)
    if cached_rows:
        return jsonify(
            {
                "status": "success",
                "source": "csv",
                "count": len(cached_rows),
                "rows": cached_rows,
            }
        )

    # 2) Live Duval Algolia
    try:
        hits = search_duval_algolia_by_parcel(parcel)
    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "source": "duval_algolia",
                "message": str(e),
            }
        ), 500

    if not hits:
        return jsonify(
            {
                "status": "success",
                "source": "none",
                "count": 0,
                "rows": [],
            }
        )

    # 3) Save one representative hit into CSV (you can change to save all)
    # We store using the parcel number that was requested
    save_lead_from_hit(parcel, hits[0], source="live_duval")

    return jsonify(
        {
            "status": "success",
            "source": "live_duval",
            "count": len(hits),
            "rows": hits,
        }
    )


# ------------------------------------------------------------------------------
# Entrypoint for local dev (Render uses gunicorn)
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
