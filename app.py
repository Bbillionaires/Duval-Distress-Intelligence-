import os
import csv
import json
import requests
from flask import Flask, jsonify, request

# --- Config from environment variables ---

ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY")  # use your SEARCH key here
ALGOLIA_INDEX_NAME = os.getenv("ALGOLIA_INDEX_NAME", "duval_property_tax")

# CSV (optional local cache)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "distress_properties.csv")

app = Flask(__name__)


# ---------- Helpers ----------

def algolia_search(query_str="", zip_code=None, min_due=None, max_due=None, hits_per_page=100):
    """
    Call Algolia's search REST API directly using `requests`.
    Returns a list of hits (dicts). If Algolia is not configured, returns [].
    """
    if not (ALGOLIA_APP_ID and ALGOLIA_API_KEY and ALGOLIA_INDEX_NAME):
        return []

    url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX_NAME}/query"

    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }

    filters = []
    numeric_filters = []

    if zip_code:
        filters.append(f"zip:{zip_code}")

    if min_due:
        try:
            numeric_filters.append(f"amount_due>={float(min_due)}")
        except ValueError:
            pass

    if max_due:
        try:
            numeric_filters.append(f"amount_due<={float(max_due)}")
        except ValueError:
            pass

    body = {
        "query": query_str or "",
        "hitsPerPage": hits_per_page,
    }

    if filters:
        body["filters"] = " AND ".join(filters)
    if numeric_filters:
        body["numericFilters"] = numeric_filters

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", [])
    except Exception:
        # On any error, just act like Algolia returned nothing
        return []


def load_csv_rows():
    """
    Load rows from the local CSV file if it exists.
    Returns a list of dicts.
    """
    if not os.path.exists(CSV_PATH):
        return []

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ---------- Routes ----------

@app.route("/")
def root():
    return "Distress Intelligence backend is online."


@app.route("/api/health")
def health():
    # CSV status
    csv_exists = os.path.exists(CSV_PATH)
    csv_size = 0
    if csv_exists:
        with open(CSV_PATH, encoding="utf-8") as f:
            csv_size = max(sum(1 for _ in f) - 1, 0)

    # Algolia config status (just checks envs, not a live ping)
    algolia_configured = bool(ALGOLIA_APP_ID and ALGOLIA_API_KEY and ALGOLIA_INDEX_NAME)

    return jsonify({
        "status": "success",
        "csv_exists": csv_exists,
        "csv_size": csv_size,
        "algolia_configured": algolia_configured,
        "algolia_app_id": ALGOLIA_APP_ID,
        "algolia_index": ALGOLIA_INDEX_NAME,
    })


@app.route("/api/search")
def search():
    """
    Search order:
    1. Try Algolia via HTTP (if configured).
    2. If Algolia returns nothing, fall back to local CSV (if present).
    """
    q = request.args.get("q", "").strip()
    zip_code = request.args.get("zip", "").strip()
    min_due = request.args.get("min_due", "").strip()
    max_due = request.args.get("max_due", "").strip()

    rows = []
    source = "none"

    # ---------- 1) Try Algolia ----------
    hits = algolia_search(
        query_str=q,
        zip_code=zip_code or None,
        min_due=min_due or None,
        max_due=max_due or None,
        hits_per_page=100,
    )

    for h in hits:
        rows.append({
            "owner": h.get("owner") or h.get("OWNER") or "",
            "property_address": h.get("property_address") or h.get("PROPERTY_ADDRESS") or "",
            "parcel": h.get("parcel") or h.get("PARCEL") or "",
            "zip": h.get("zip") or h.get("ZIP") or "",
            "distress_type": h.get("distress_type") or h.get("DISTRESS_TYPE") or "Tax",
            "amount_due": h.get("amount_due") or h.get("AMOUNT_DUE") or "",
        })

    if rows:
        source = "algolia"

    # ---------- 2) Fallback: local CSV ----------
    if not rows:
        csv_rows = load_csv_rows()
        for r in csv_rows:
            # If a query was provided, match parcel or owner
            if q:
                parcel = r.get("parcel", "") or r.get("PARCEL", "")
                owner = r.get("owner", "") or r.get("OWNER", "")
                if q not in parcel and q.lower() not in owner.lower():
                    continue

            # Zip filter
            if zip_code:
                r_zip = r.get("zip", "") or r.get("ZIP", "")
                if r_zip != zip_code:
                    continue

            # Amount-due filters
            r_amt_raw = r.get("amount_due") or r.get("AMOUNT_DUE")
            try:
                r_amt = float(r_amt_raw) if r_amt_raw not in (None, "",) else None
            except ValueError:
                r_amt = None

            if min_due and r_amt is not None:
                try:
                    if r_amt < float(min_due):
                        continue
                except ValueError:
                    pass

            if max_due and r_amt is not None:
                try:
                    if r_amt > float(max_due):
                        continue
                except ValueError:
                    pass

            rows.append({
                "owner": r.get("owner", "") or r.get("OWNER", ""),
                "property_address": r.get("property_address", "") or r.get("PROPERTY_ADDRESS", ""),
                "parcel": r.get("parcel", "") or r.get("PARCEL", ""),
                "zip": r.get("zip", "") or r.get("ZIP", ""),
                "distress_type": r.get("distress_type", "") or r.get("DISTRESS_TYPE", "") or "Tax",
                "amount_due": r_amt_raw or "",
            })

        if rows:
            source = "csv"

    return jsonify({
        "status": "success",
        "count": len(rows),
        "rows": rows,
        "source": source,
    })


if __name__ == "__main__":
    # For local debugging only; Render will use gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
