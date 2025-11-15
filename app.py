import os
import csv
import json
from datetime import datetime, timedelta

from flask import Flask, jsonify, request
from flask_cors import CORS
from algoliasearch.search_client import SearchClient

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

# Read Render env vars (ALGOLIA_*) and fall back to older names if present
ALG_APP_ID = os.getenv("ALGOLIA_APP_ID") or os.getenv("ALG_APP_ID")
ALG_API_KEY = os.getenv("ALGOLIA_API_KEY") or os.getenv("ALG_API_KEY")
ALG_INDEX_NAME = os.getenv("ALGOLIA_INDEX") or os.getenv("ALG_INDEX")

CSV_PATH = os.getenv("CSV_PATH", "leads.csv")
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

# ------------------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

# ------------------------------------------------------------------------------
# Algolia client
# ------------------------------------------------------------------------------

algolia_client = None
algolia_index = None

if ALG_APP_ID and ALG_API_KEY and ALG_INDEX_NAME:
    try:
        algolia_client = SearchClient.create(ALG_APP_ID, ALG_API_KEY)
        algolia_index = algolia_client.init_index(ALG_INDEX_NAME)
    except Exception:
        # If something is wrong with keys, we keep algolia_index = None
        algolia_index = None


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


def save_lead_from_hit(hit: dict, source: str):
    """
    Save a single Algolia hit into CSV (if not already present).
    We only store a small subset of columns so CSV stays clean.
    """
    parcel = hit.get("parcel") or hit.get("objectID") or ""
    if not parcel:
        return

    # Check for duplicate parcel in CSV
    rows = load_csv_rows()
    for row in rows:
        if row.get("parcel") == parcel:
            return  # already stored

    row = {
        "parcel": parcel,
        "owner_name": hit.get("owner_name", ""),
        "display_name": hit.get("display_name", ""),
        "address": hit.get("address", ""),
        "city": hit.get("city", ""),
        "state": hit.get("state", ""),
        "zip": hit.get("zip", ""),
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
# Algolia helper
# ------------------------------------------------------------------------------

def search_algolia_by_parcel(parcel: str):
    """
    Search Algolia index for a parcel.
    The index must have an attribute named 'parcel' for the filter to work.
    """
    if not algolia_index:
        return []

    try:
        # Using filters on parcel field
        res = algolia_index.search(
            "",
            {"filters": f'parcel:"{parcel}"'}
        )
        hits = res.get("hits", [])
        return hits
    except Exception:
        return []


# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.route("/")
def root():
    return "Duval Distress Intelligence backend is online."


@app.route("/api/health")
def health():
    info = {
        "status": "ok",
        "algolia_configured": bool(algolia_index),
        "algolia_app_id": ALG_APP_ID or "",
        "algolia_index": ALG_INDEX_NAME or "",
        "cache_days": CACHE_DAYS,
        "csv_exists": csv_exists(),
        "csv_size": len(load_csv_rows()) if csv_exists() else 0,
    }
    return jsonify(info)


@app.route("/api/config")
def config():
    """
    Simple config endpoint you can hit from the browser to confirm env + CSV.
    """
    data = {
        "status": "success",
        "algolia_configured": bool(algolia_index),
        "algolia_app_id": ALG_APP_ID or "",
        "algolia_index": ALG_INDEX_NAME or "",
        "cache_days": CACHE_DAYS,
        "csv_exists": csv_exists(),
        "csv_size": len(load_csv_rows()) if csv_exists() else 0,
    }
    return jsonify(data)


@app.route("/api/parcel")
def parcel_lookup():
    """
    Main endpoint:
    - First checks CSV cache for this parcel, only if younger than CACHE_DAYS
    - If not found or stale, queries Algolia
    - Saves any fresh Algolia hits into CSV (no duplicates)
    """
    parcel = request.args.get("parcel", "").strip()
    if not parcel:
        return jsonify(
            {"status": "error", "message": "Missing ?parcel= parameter"}
        ), 400

    # 1) CSV cache first
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

    # 2) Live Algolia lookup
    hits = search_algolia_by_parcel(parcel)
    if not hits:
        return jsonify(
            {
                "status": "success",
                "source": "none",
                "count": 0,
                "rows": [],
            }
        )

    # Save each hit into CSV for future cache use
    for hit in hits:
        save_lead_from_hit(hit, source="live")

    return jsonify(
        {
            "status": "success",
            "source": "live",
            "count": len(hits),
            "rows": hits,
        }
    )


# ------------------------------------------------------------------------------
# Entrypoint for gunicorn
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
