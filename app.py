    import os
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import csv
import requests
from algoliasearch.search_client import SearchClient
from bs4 import BeautifulSoup

# --------------------------------------
# CONFIG
# --------------------------------------

ALGOLIA_APP_ID = "EG68MYCIPK"
ALGOLIA_SEARCH_KEY = "71d337a60ec2815979ec0251572482a0"
ALGOLIA_ADMIN_KEY = "8be43ca33a4046f065a8c3831bb91e99"
ALGOLIA_INDEX = "duval_parcels"

CSV_FILE = "duval_data.csv"

# --------------------------------------
# INIT
# --------------------------------------

app = Flask(__name__)
CORS(app)

client = SearchClient.create(ALGOLIA_APP_ID, ALGOLIA_ADMIN_KEY)
index = client.init_index(ALGOLIA_INDEX)

# --------------------------------------
# CSV HELPERS
# --------------------------------------

def load_csv_data():
    rows = []
    if not os.path.exists(CSV_FILE):
        return rows
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def save_csv_data(rows):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "parcel",
            "owner",
            "address",
            "zip",
            "distress_type"
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

# -------------------------------------------------
# LIVE SCRAPER (FALLBACK)
# -------------------------------------------------

def scrape_duval(parcel_id):
    """
    Scrapes Duval County Property Tax website when data
    is not found in Algolia or CSV.
    """

    SEARCH_URL = "https://county-taxes.net/api/search"

    payload = {
        "requests": [
            {
                "indexName": "fl-duval.property_tax",
                "params": f"query={parcel_id}"
            }
        ]
    }

    headers = {
        "x-algolia-agent": "Algolia for JavaScript",
        "x-algolia-application-id": "0LWZO52LS2",
        "x-algolia-api-key": "c0745578b56854a1b90ed57b63fbf0ba"
    }

    try:
        r = requests.post(
            "https://0lwzo52ls2-dsn.algolia.net/1/indexes/*/queries",
            json=payload,
            headers=headers,
            timeout=10
        )
        data = r.json()

        hits = data["results"][0]["hits"]
        if not hits:
            return None

        hit = hits[0]

        return {
            "parcel": hit.get("account"),
            "owner": hit.get("owner"),
            "address": hit.get("address"),
            "zip": hit.get("zip"),
            "distress_type": "Tax"
        }
    except:
        return None

# -------------------------------------------------
# SEARCH LOGIC (Search → CSV → Algolia → Live scrape)
# -------------------------------------------------

def full_lookup(parcel_id):
    # 1 → Search Algolia first
    try:
        res = index.search(parcel_id)
        if res.get("hits"):
            return res["hits"][0]
    except:
        pass

    # 2 → Search CSV backup
    csv_rows = load_csv_data()
    for r in csv_rows:
        if r["parcel"] == parcel_id:
            return r

    # 3 → Scrape live
    scraped = scrape_duval(parcel_id)
    if scraped:
        # Save to Algolia
        index.save_object({**scraped, "objectID": scraped["parcel"]})

        # Save to CSV (dedupe)
        existing = load_csv_data()
        if scraped["parcel"] not in [x["parcel"] for x in existing]:
            existing.append(scraped)
            save_csv_data(existing)

        return scraped

    return None

# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if q == "":
        return jsonify({"count": 0, "rows": [], "status": "success"})

    result = full_lookup(q)
    if not result:
        return jsonify({"count": 0, "rows": [], "status": "success"})

    return jsonify({"count": 1, "rows": [result], "status": "success"})


@app.route("/api/export")
def api_export():
    if not os.path.exists(CSV_FILE):
        return jsonify({"error": "CSV not found"})

    return send_file(CSV_FILE, as_attachment=True)


@app.route("/api/health")
def health():
    csv_exists = os.path.exists(CSV_FILE)
    csv_size = len(load_csv_data()) if csv_exists else 0

    return jsonify({
        "status": "success",
        "algolia_app_id": ALGOLIA_APP_ID,
        "algolia_index": ALGOLIA_INDEX,
        "csv_exists": csv_exists,
        "csv_size": csv_size
    })


@app.route("/")
def index_page():
    return "Backend online. UI coming soon."


# -------------------------------------------------
# START
# -------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
