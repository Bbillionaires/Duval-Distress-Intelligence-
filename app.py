import os
import csv
import json
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify
# CLASSIC ALGOLIA CLIENT (works with older versions)
from algoliasearch import algoliasearch

app = Flask(__name__)

# ------------------------------------------------
# CONFIG
# ------------------------------------------------

# CSV file on disk (for caching leads + dedupe)
CSV_FILE = os.getenv("LEADS_CSV_FILE", "leads.csv")

# Days before we ignore cache and hit Algolia again
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "30"))

# Algolia credentials (must be set in Render env)
ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY")
ALGOLIA_INDEX = os.getenv("ALGOLIA_INDEX")

# Exact CSV columns (NO extra fields allowed)
CSV_FIELDS = [
    "parcel",
    "owner_name",
    "mailing_address",
    "mailing_city",
    "mailing_state",
    "mailing_zip",
    "last_updated",
    "raw_json",
]


# ------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------

def get_algolia_index():
    """
    Create Algolia client & index instance using the classic client.
    Raises RuntimeError if env vars are missing.
    """
    if not (ALGOLIA_APP_ID and ALGOLIA_API_KEY and ALGOLIA_INDEX):
        raise RuntimeError("Algolia env vars ALGOLIA_APP_ID, ALGOLIA_API_KEY, ALGOLIA_INDEX must be set")
    client = algoliasearch.Client(ALGOLIA_APP_ID, ALGOLIA_API_KEY)
    return client.init_index(ALGOLIA_INDEX)


def ensure_csv_exists():
    """Create CSV with header if it does not exist."""
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def load_all_rows():
    """Return all rows from CSV as list of dicts."""
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_all_rows(rows):
    """Overwrite CSV with provided list of row dicts."""
    ensure_csv_exists()
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            # IMPORTANT: only write known fields to avoid ValueError
            filtered = {k: row.get(k, "") for k in CSV_FIELDS}
            writer.writerow(filtered)


def find_cached(parcel):
    """
    Look for parcel in CSV. Return (row, source_string).

    - If found & fresh (<= CACHE_DAYS) → (row, "csv")
    - If found but expired → (None, "expired")
    - If not found → (None, "none")
    """
    rows = load_all_rows()
    for row in rows:
        if row.get("parcel") == parcel:
            ts = row.get("last_updated")
            if not ts:
                return None, "expired"
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                return None, "expired"
            if datetime.now(timezone.utc) - dt <= timedelta(days=CACHE_DAYS):
                return row, "csv"
            else:
                return None, "expired"
    return None, "none"


def upsert_lead(lead):
    """
    Insert or update a lead in the CSV (by parcel).
    """
    rows = load_all_rows()
    replaced = False
    for i, row in enumerate(rows):
        if row.get("parcel") == lead.get("parcel"):
            rows[i] = lead
            replaced = True
            break
    if not replaced:
        rows.append(lead)
    save_all_rows(rows)


def lead_from_hit(parcel, hit):
    """
    Normalize an Algolia hit into our CSV schema.
    Tries a few common field names safely.
    """
    owner = (
        hit.get("display_name")
        or hit.get("owner_name")
        or hit.get("name")
        or ""
    )

    # Address may be a plain string or nested object
    address = ""
    address_field = hit.get("address")
    if isinstance(address_field, dict):
        address = address_field.get("value", "") or ""
    else:
        address = address_field or ""

    city = hit.get("city", "") or hit.get("mailing_city", "")
    state = hit.get("state", "") or hit.get("mailing_state", "")
    zipcode = hit.get("zip", "") or hit.get("zipcode", "") or hit.get("mailing_zip", "")

    return {
        "parcel": parcel,
        "owner_name": owner,
        "mailing_address": address,
        "mailing_city": city,
        "mailing_state": state,
        "mailing_zip": zipcode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "raw_json": json.dumps(hit),
    }


# ------------------------------------------------
# ROUTES
# ------------------------------------------------

@app.route("/")
def root():
    return "Distress Intelligence backend is online."


@app.route("/api/health")
def health():
    """Health + CSV status."""
    exists = os.path.exists(CSV_FILE)
    size = 0
    if exists:
        rows = load_all_rows()
        size = len(rows)

    return jsonify(
        {
            "cache_days": CACHE_DAYS,
            "csv_exists": exists,
            "csv_size": size,
            "status": "success",
        }
    )


@app.route("/api/parcel")
def parcel_lookup():
    """
    Parcel lookup flow:

    1. Check CSV cache.
    2. If not fresh, hit Algolia.
    3. Save / update CSV.
    4. Return standard shape: {count, rows, source, status}.
    """
    parcel = (request.args.get("parcel") or "").strip()

    if not parcel:
        return (
            jsonify(
                {
                    "count": 0,
                    "rows": [],
                    "source": "none",
                    "status": "error",
                    "message": "parcel query parameter is required",
                }
            ),
            400,
        )

    # Step 1: CSV cache
    cached, cache_source = find_cached(parcel)
    if cached is not None:
        return jsonify(
            {
                "count": 1,
                "rows": [cached],
                "source": "csv",
                "status": "success",
            }
        )

    # Step 2: Live Algolia request
    try:
        index = get_algolia_index()
        res = index.search(parcel)
        hits = res.get("hits", [])
    except Exception as e:
        return (
            jsonify(
                {
                    "count": 0,
                    "rows": [],
                    "source": "error",
                    "status": "error",
                    "message": f"Algolia error: {e}",
                }
            ),
            500,
        )

    if not hits:
        # No results from Algolia; don't cache anything
        return (
            jsonify(
                {
                    "count": 0,
                    "rows": [],
                    "source": "live",
                    "status": "not_found",
                }
            ),
            404,
        )

    # Use top hit
    hit = hits[0]
    lead = lead_from_hit(parcel, hit)
    upsert_lead(lead)

    return jsonify(
        {
            "count": 1,
            "rows": [lead],
            "source": "live",
            "status": "success",
        }
    )


@app.route("/api/search")
def search():
    """
    Free-text search endpoint (owner / address / anything Algolia supports).
    Does NOT rely on cache – pure live search.
    """
    query = (request.args.get("q") or "").strip()

    if not query:
        return (
            jsonify(
                {
                    "count": 0,
                    "rows": [],
                    "source": "none",
                    "status": "error",
                    "message": "q query parameter is required",
                }
            ),
            400,
        )

    try:
        index = get_algolia_index()
        res = index.search(query, {"hitsPerPage": 20})
        hits = res.get("hits", [])
    except Exception as e:
        return (
            jsonify(
                {
                    "count": 0,
                    "rows": [],
                    "source": "error",
                    "status": "error",
                    "message": f"Algolia error: {e}",
                }
            ),
            500,
        )

    rows = []
    for hit in hits:
        parcel = (
            hit.get("parcel")
            or hit.get("external_id")
            or hit.get("external_id_tokens")
            or ""
        )
        rows.append(lead_from_hit(parcel, hit))

    return jsonify(
        {
            "count": len(rows),
            "rows": rows,
            "source": "live",
            "status": "success",
        }
    )


@app.route("/app")
@app.route("/app/")
def ui():
    """
    Simple mobile-friendly HTML UI that hits /api/parcel and /api/search.
    """
    html = """
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Duval Distress Intelligence</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 16px; background:#f5f5f5; }
            h1 { font-size: 20px; margin-bottom: 8px; }
            label { display:block; margin-top:12px; font-size:14px; }
            input[type=text] { width:100%; padding:8px; border-radius:6px; border:1px solid #ccc; box-sizing:border-box; }
            button { margin-top:12px; padding:10px 14px; border:none; border-radius:6px; background:#2563eb; color:white; font-size:14px; }
            button:disabled { opacity:0.6; }
            pre { background:white; padding:10px; border-radius:6px; overflow-x:auto; font-size:12px; }
            .card { background:white; border-radius:8px; padding:12px; margin-top:16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
            .small { font-size:12px; color:#6b7280; }
        </style>
    </head>
    <body>
        <h1>Duval Distress Intelligence</h1>
        <div class="small">Backend quick UI (mobile-friendly).</div>

        <div class="card">
            <label>Parcel number</label>
            <input id="parcel-input" type="text" placeholder="e.g. 0301470432">
            <button onclick="lookupParcel()">Lookup parcel</button>

            <label>Owner / address search</label>
            <input id="search-input" type="text" placeholder="Owner name or street">
            <button onclick="runSearch()">Search</button>

            <div id="status" class="small" style="margin-top:8px;"></div>
        </div>

        <div class="card">
            <div class="small">Result</div>
            <pre id="result">(nothing yet)</pre>
        </div>

        <script>
        async function lookupParcel() {
            const parcel = document.getElementById('parcel-input').value.trim();
            if (!parcel) {
                alert('Enter a parcel number');
                return;
            }
            setStatus('Looking up parcel ' + parcel + ' ...');
            try {
                const resp = await fetch('/api/parcel?parcel=' + encodeURIComponent(parcel));
                const data = await resp.json();
                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
                setStatus('Done (source: ' + (data.source || 'unknown') + ', status: ' + (data.status || 'unknown') + ')');
            } catch (err) {
                setStatus('Error: ' + err);
            }
        }

        async function runSearch() {
            const q = document.getElementById('search-input').value.trim();
            if (!q) {
                alert('Enter something to search');
                return;
            }
            setStatus('Searching for "' + q + '" ...');
            try {
                const resp = await fetch('/api/search?q=' + encodeURIComponent(q));
                const data = await resp.json();
                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
                setStatus('Done (rows: ' + (data.count || 0) + ')');
            } catch (err) {
                setStatus('Error: ' + err);
            }
        }

        function setStatus(msg) {
            document.getElementById('status').textContent = msg;
        }
        </script>
    </body>
    </html>
    """
    return html


# For local testing; Render uses gunicorn app:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
