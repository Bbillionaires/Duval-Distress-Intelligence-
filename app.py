import os
import csv
import json
from datetime import datetime, timedelta

from flask import Flask, request, jsonify

from algoliasearch.search_client import SearchClient

# -----------------------------
# CONFIG
# -----------------------------

ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX", "duval_property_tax")

CSV_PATH = os.environ.get("LEADS_CSV_PATH", "leads.csv")
CACHE_DAYS = int(os.environ.get("CACHE_DAYS", "30"))

# CSV columns – everything we’ll store
CSV_FIELDS = [
    "parcel",
    "owner_name",
    "mailing_address",
    "property_address",
    "city",
    "state",
    "zip",
    "land_value",
    "total_value",
    "last_updated",
    "source",
    "raw_json",
]

app = Flask(__name__)

# -----------------------------
# ALGOLIA CLIENT
# -----------------------------

algolia_index = None
if ALGOLIA_APP_ID and ALGOLIA_API_KEY and ALGOLIA_INDEX_NAME:
    try:
        client = SearchClient.create(ALGOLIA_APP_ID, ALGOLIA_API_KEY)
        algolia_index = client.init_index(ALGOLIA_INDEX_NAME)
    except Exception as e:
        # Don’t crash the app – just log
        print("Error initialising Algolia:", e)


# -----------------------------
# CSV HELPERS
# -----------------------------

def _ensure_row_fields(row: dict) -> dict:
    """Return a row dict containing ONLY the CSV_FIELDS keys."""
    return {field: row.get(field, "") for field in CSV_FIELDS}


def upsert_csv(row: dict) -> None:
    """
    Insert or update a row in the CSV (by parcel).
    No duplicates – if parcel exists, it gets replaced.
    """
    row = _ensure_row_fields(row)
    rows = []

    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for existing in reader:
                if existing.get("parcel") == row["parcel"]:
                    continue
                rows.append(existing)

    rows.append(row)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def get_cached_lead(parcel: str):
    """
    Return (row, stale_bool) or (None, None) if not in CSV.
    """
    if not os.path.exists(CSV_PATH):
        return None, None

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("parcel") == parcel:
                # determine if stale (> CACHE_DAYS old)
                ts = row.get("last_updated") or ""
                try:
                    dt = datetime.fromisoformat(ts)
                    stale = dt < datetime.utcnow() - timedelta(days=CACHE_DAYS)
                except Exception:
                    stale = True
                return row, stale

    return None, None


def csv_stats():
    """Return (exists_bool, size_int)."""
    if not os.path.exists(CSV_PATH):
        return False, 0

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        size = sum(1 for _ in reader)
    return True, size


# -----------------------------
# PARSING ALGOLIA RECORDS
# -----------------------------

def parse_algolia_hit(hit: dict, source: str) -> dict:
    """
    Flatten one Algolia hit into our CSV/API format.
    We keep the full raw JSON in raw_json for anything we miss.
    """
    parcel = hit.get("parcel") or hit.get("Parcel") or ""

    owner_name = hit.get("display_name", "")

    mailing_address = ""
    property_address = ""
    city = ""
    state = ""
    zip_code = ""
    land_value = hit.get("land_value", "")
    total_value = hit.get("total_value", "")

    # child_groups structure is where a lot of Duval data lives
    child_groups = (hit.get("child_groups") or {}).get("children", [])

    for child in child_groups:
        ext_type = (child.get("external_type") or "").lower()
        cp_raw = child.get("custom_parameters") or "{}"
        try:
            cp = json.loads(cp_raw)
        except Exception:
            cp = {}

        # Mailing address (owner)
        if not mailing_address and (
            "owner address" in ext_type
            or "owneraddress" in ext_type
            or ("owner" in ext_type and "address" in ext_type)
        ):
            mailing_address = cp.get("address1", "")
            city = cp.get("city", "")
            state = cp.get("province", "")
            zip_code = cp.get("zip", "")

        # Property / situs address (if separate)
        if not property_address and (
            "situs" in ext_type
            or "property address" in ext_type
            or "location address" in ext_type
        ):
            property_address = cp.get("address1", "")

    if not property_address:
        property_address = mailing_address

    now_iso = datetime.utcnow().isoformat()

    row = {
        "parcel": parcel,
        "owner_name": owner_name,
        "mailing_address": mailing_address,
        "property_address": property_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "land_value": land_value,
        "total_value": total_value,
        "last_updated": now_iso,
        "source": source,
        "raw_json": json.dumps(hit),
    }
    return _ensure_row_fields(row)


# -----------------------------
# ROUTES – HEALTH & ROOT
# -----------------------------

@app.route("/")
def root():
    return "Distress Intelligence backend is online."


@app.route("/api/health")
def health():
    csv_exists, csv_size = csv_stats()
    return jsonify(
        {
            "status": "success",
            "algolia_app_id": ALGOLIA_APP_ID,
            "algolia_index": ALGOLIA_INDEX_NAME,
            "algolia_configured": bool(algolia_index),
            "csv_exists": csv_exists,
            "csv_size": csv_size,
            "cache_days": CACHE_DAYS,
        }
    )


# -----------------------------
# OPTION B: API ENDPOINTS
# -----------------------------

@app.route("/api/parcel")
def parcel_lookup():
    """
    Look up a single parcel.
    - First checks CSV cache
    - If stale or missing, pulls live from Algolia
    - Saves/updates CSV (no duplicates)
    - 30-day refresh is handled here
    """
    if not algolia_index:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Algolia is not configured on the server.",
                }
            ),
            500,
        )

    parcel = (request.args.get("parcel") or "").strip()
    if not parcel:
        return (
            jsonify({"status": "error", "message": "Query parameter 'parcel' required"}),
            400,
        )

    # 1) Check CSV cache
    cached, stale = get_cached_lead(parcel)
    if cached and not stale:
        cached["source"] = "cache"
        return jsonify(
            {"status": "success", "count": 1, "rows": [cached], "source": "cache"}
        )

    # 2) Pull live from Algolia (or refresh stale)
    try:
        res = algolia_index.search(parcel, {"hitsPerPage": 1})
        hits = res.get("hits", [])
    except Exception as e:
        # If Algolia fails but we have cache, return cache
        if cached:
            cached["source"] = "cache_error"
            return jsonify(
                {
                    "status": "partial",
                    "message": str(e),
                    "count": 1,
                    "rows": [cached],
                    "source": "cache_error",
                }
            )
        return (
            jsonify({"status": "error", "message": f"Algolia error: {e}"}),
            500,
        )

    if not hits:
        # No live result – if we had stale cache, still return it
        if cached:
            cached["source"] = "cache_stale"
            return jsonify(
                {
                    "status": "success",
                    "count": 1,
                    "rows": [cached],
                    "source": "cache_stale",
                }
            )
        return jsonify({"status": "success", "count": 0, "rows": [], "source": "live"})

    lead = parse_algolia_hit(hits[0], source="live")
    upsert_csv(lead)

    return jsonify(
        {"status": "success", "count": 1, "rows": [lead], "source": "live"}
    )


@app.route("/api/search")
def search():
    """
    General search endpoint (owner name, address, etc.)
    Returns up to 20 leads. Each lead is also written/updated in CSV.
    """
    if not algolia_index:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Algolia is not configured on the server.",
                }
            ),
            500,
        )

    q = (request.args.get("q") or "").strip()
    if not q:
        return (
            jsonify({"status": "error", "message": "Query parameter 'q' required"}),
            400,
        )

    try:
        res = algolia_index.search(q, {"hitsPerPage": 20})
        hits = res.get("hits", [])
    except Exception as e:
        return (
            jsonify({"status": "error", "message": f"Algolia error: {e}"}),
            500,
        )

    rows = []
    for hit in hits:
        lead = parse_algolia_hit(hit, source="live")
        rows.append(lead)
        upsert_csv(lead)

    return jsonify(
        {"status": "success", "count": len(rows), "rows": rows, "source": "live"}
    )


# -----------------------------
# OPTION A: SIMPLE FRONTEND UI
# -----------------------------

@app.route("/app")
def ui():
    """
    Very simple HTML UI so you can use this from your phone:
    - Parcel lookup
    - Free-text search (owner / address)
    """
    html = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Duval Distress Intelligence</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background:#f5f5f5; }
      h1 { font-size: 1.4rem; margin-bottom: 8px; }
      .card { background:#fff; border-radius:12px; padding:12px 14px; margin-bottom:14px; box-shadow:0 2px 6px rgba(0,0,0,0.06); }
      label { font-size:0.9rem; font-weight:600; display:block; margin-bottom:4px; }
      input { width:100%; padding:10px; border-radius:8px; border:1px solid #ccc; font-size:1rem; box-sizing:border-box; }
      button { margin-top:8px; padding:10px 14px; border:none; border-radius:8px; background:#111827; color:#fff; font-weight:600; font-size:0.95rem; }
      button:disabled { opacity:0.6; }
      .row { margin-top:10px; font-size:0.9rem; }
      .row strong { display:inline-block; min-width:110px; }
      pre { white-space:pre-wrap; word-wrap:break-word; font-size:0.8rem; background:#f9fafb; border-radius:8px; padding:8px; max-height:260px; overflow:auto; }
      .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.75rem; background:#e5e7eb; margin-left:4px; }
    </style>
  </head>
  <body>
    <h1>Duval Distress Intelligence</h1>
    <div class="card">
      <label for="parcel">Parcel lookup</label>
      <input id="parcel" placeholder="e.g. 0301470432" />
      <button onclick="lookupParcel()">Search by parcel</button>
    </div>

    <div class="card">
      <label for="query">Owner / address search</label>
      <input id="query" placeholder="Owner name, street, etc." />
      <button onclick="searchLeads()">Search by text</button>
    </div>

    <div id="result" class="card" style="display:none;"></div>

    <script>
      async function lookupParcel() {
        const parcel = document.getElementById('parcel').value.trim();
        if (!parcel) { alert('Enter a parcel number'); return; }
        await callApi('/api/parcel?parcel=' + encodeURIComponent(parcel));
      }

      async function searchLeads() {
        const q = document.getElementById('query').value.trim();
        if (!q) { alert('Enter a search term'); return; }
        await callApi('/api/search?q=' + encodeURIComponent(q));
      }

      async function callApi(url) {
        const card = document.getElementById('result');
        card.style.display = 'block';
        card.innerHTML = 'Loading...';

        try {
          const res = await fetch(url);
          const data = await res.json();

          if (data.status !== 'success' && data.status !== 'partial') {
            card.innerHTML = '<strong>Error:</strong> ' + (data.message || 'Unknown error');
            return;
          }

          if (!data.rows || data.rows.length === 0) {
            card.innerHTML = 'No records found.';
            return;
          }

          const first = data.rows[0];

          const main = [
            '<div class="row"><strong>Source:</strong> ' + (data.source || first.source || '') + '</div>',
            '<div class="row"><strong>Parcel:</strong> ' + (first.parcel || '') + '</div>',
            '<div class="row"><strong>Owner:</strong> ' + (first.owner_name || '') + '</div>',
            '<div class="row"><strong>Mailing:</strong> ' + (first.mailing_address || '') + '</div>',
            '<div class="row"><strong>Property:</strong> ' + (first.property_address || '') + '</div>',
            '<div class="row"><strong>City/State:</strong> ' + (first.city || "") + " " + (first.state || "") + '</div>',
            '<div class="row"><strong>Zip:</strong> ' + (first.zip || '') + '</div>',
            '<div class="row"><strong>Land value:</strong> ' + (first.land_value || '') + '</div>',
            '<div class="row"><strong>Total value:</strong> ' + (first.total_value || '') + '</div>',
            '<div class="row"><strong>Last updated:</strong> ' + (first.last_updated || '') + '</div>',
            '<div class="row"><strong>Records returned:</strong> ' + (data.count || data.rows.length) + '</div>'
          ].join('');

          const rawJson = first.raw_json ? JSON.stringify(JSON.parse(first.raw_json), null, 2) : JSON.stringify(first, null, 2);

          card.innerHTML = main + '<hr><div style="margin-top:8px;font-size:0.8rem;font-weight:600;">Raw JSON</div><pre>' + rawJson + '</pre>';
        } catch (e) {
          card.innerHTML = 'Request failed: ' + e;
        }
      }
    </script>
  </body>
</html>
    """
    return html
