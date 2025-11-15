import os
import csv
import json
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# -------------------------
# Config
# -------------------------
CSV_FILE = "duval_leads.csv"
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

# Duval county's Algolia (from DevTools)
ALGOLIA_APP_ID = "0LWZO52LS2"
ALGOLIA_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
ALGOLIA_INDEX = "fl-duval.property_tax"
ALGOLIA_ENDPOINT = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"


# -------------------------
# CSV helpers
# -------------------------
def load_csv():
    """Return a dict: {parcel: row_dict}."""
    if not os.path.exists(CSV_FILE):
        return {}

    leads = {}
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parcel = row.get("parcel")
            if parcel:
                leads[parcel] = row
    return leads


def save_csv(leads):
    """Persist the leads dict back to CSV, with a fixed schema."""
    fieldnames = ["parcel", "raw_json", "last_updated"]

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for parcel, data in leads.items():
            row = {field: data.get(field, "") for field in fieldnames}
            writer.writerow(row)


def get_cached_lead(parcel: str):
    """Return cached row if it exists and is fresh, otherwise None."""
    leads = load_csv()
    row = leads.get(parcel)
    if not row:
        return None

    last_updated = row.get("last_updated")
    if not last_updated:
        return None

    try:
        dt = datetime.fromisoformat(last_updated)
    except Exception:
        return None

    if datetime.utcnow() - dt > timedelta(days=CACHE_DAYS):
        # too old, force refresh
        return None

    return row


def cache_lead(parcel: str, hit: dict):
    """Store / update a parcel in CSV cache."""
    leads = load_csv()
    leads[parcel] = {
        "parcel": parcel,
        "raw_json": json.dumps(hit, ensure_ascii=False),
        "last_updated": datetime.utcnow().isoformat(),
    }
    save_csv(leads)


# -------------------------
# External fetch (Duval Algolia)
# -------------------------
def fetch_from_duval(parcel: str):
    """
    Call Duval's Algolia index directly and return the first hit (or None).
    """
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }

    body = {
        "requests": [
            {
                "indexName": ALGOLIA_INDEX,
                # minimal params string; Algolia parses like a querystring
                "params": f"query={parcel}&hitsPerPage=5",
            }
        ]
    }

    try:
        resp = requests.post(ALGOLIA_ENDPOINT, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # Log to server logs for debugging
        print("Error calling Algolia:", e)
        return None

    try:
        results = data.get("results", [])
        if not results:
            return None
        hits = results[0].get("hits", [])
        if not hits:
            return None
        return hits[0]  # just take first hit
    except Exception:
        return None


# -------------------------
# Routes
# -------------------------
@app.route("/")
def root():
    return "Distress Intelligence backend is online."


@app.route("/api/health")
def health():
    csv_exists = os.path.exists(CSV_FILE)
    csv_size = 0
    if csv_exists:
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            csv_size = sum(1 for _ in f) - 1  # minus header

    return jsonify(
        {
            "status": "success",
            "csv_exists": csv_exists,
            "csv_size": max(csv_size, 0),
            "cache_days": CACHE_DAYS,
        }
    )


@app.route("/api/parcel")
def parcel_lookup():
    parcel = request.args.get("parcel", "").strip()
    if not parcel:
        return jsonify({"status": "error", "message": "parcel is required"}), 400

    # 1. Try cache
    cached = get_cached_lead(parcel)
    if cached:
        return jsonify(
            {
                "status": "success",
                "source": "cache",
                "count": 1,
                "rows": [cached],
            }
        )

    # 2. Fetch live from Duval Algolia
    hit = fetch_from_duval(parcel)
    if not hit:
        return jsonify(
            {
                "status": "success",
                "source": "live",
                "count": 0,
                "rows": [],
            }
        )

    # 3. Cache and return
    cache_lead(parcel, hit)

    row = get_cached_lead(parcel)  # now guaranteed to exist & be normalized
    return jsonify(
        {
            "status": "success",
            "source": "live",
            "count": 1,
            "rows": [row],
        }
    )


if __name__ == "__main__":
    # for local testing; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
