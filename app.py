import os
import csv
import json
import logging
import requests
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

# =====================================================
#  CONFIGURATION
# =====================================================
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("distress")

DATA_FILE = "data/leads.csv"


# =====================================================
#  UTILITY FUNCTIONS
# =====================================================
def read_leads():
    """Load leads from CSV if available."""
    leads = []
    if not os.path.exists(DATA_FILE):
        logger.warning(f"File not found: {DATA_FILE}")
        return leads

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append(row)
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")
    return leads


def _read_csv_from_url(url: str):
    """Download CSV text from a GitHub raw URL after trimming spaces/newlines."""
    url = (url or "").strip()
    if not url.startswith("http"):
        raise ValueError(f"Invalid GH_RAW_URL: {url!r}")
    logger.info(f"Fetching CSV from: {url}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    text = r.text
    if not text.strip():
        raise ValueError("Downloaded CSV is empty")
    return text


# =====================================================
#  ROUTES
# =====================================================
@app.get("/")
def index():
    """Serve index.html if it exists."""
    cwd = os.getcwd()
    path = os.path.join(cwd, "index.html")
    exists = os.path.exists(path)
    logger.info(f"Serving index. cwd={cwd} root={path} exists={exists}")
    if exists:
        return send_from_directory(cwd, "index.html")
    return "Index file not found", 404


@app.get("/api/leads")
def api_leads():
    """Return filtered leads from local CSV."""
    leads = read_leads()
    if not leads:
        return jsonify([])

    # Optional filters
    q = request.args.get("q", "").lower()
    zip_filter = request.args.get("zip", "")
    min_amount = request.args.get("min_amount", "")
    max_amount = request.args.get("max_amount", "")
    source_filter = request.args.get("sources", "")

    filtered = []
    for lead in leads:
        # Match query or zip
        if q and q not in json.dumps(lead).lower():
            continue
        if zip_filter and not str(lead.get("zip", "")).startswith(zip_filter):
            continue

        # Filter by amount due
        amt = 0
        try:
            amt = float(lead.get("amount_due", 0))
        except:
            pass
        if min_amount and amt < float(min_amount):
            continue
        if max_amount and amt > float(max_amount):
            continue

        # Filter by distress source
        if source_filter and source_filter.lower() not in json.dumps(lead).lower():
            continue

        filtered.append(lead)

    return jsonify(filtered)


@app.get("/api/refresh")
def api_refresh():
    """Fetch latest leads.csv from GH_RAW_URL and overwrite data/leads.csv"""
    try:
        raw_url = os.getenv("GH_RAW_URL", "").strip()
        if not raw_url:
            raise ValueError("Missing GH_RAW_URL environment variable")

        csv_text = _read_csv_from_url(raw_url)

        os.makedirs("data", exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8", newline="") as f:
            f.write(csv_text)

        size = len(csv_text.encode("utf-8"))
        logger.info(f"Updated leads.csv ({size} bytes)")
        return jsonify({
            "status": "success",
            "message": f"Leads updated successfully ({size} bytes).",
            "bytes": size,
            "source": raw_url
        })

    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400


@app.get("/debug/file/data/leads.csv")
def debug_file():
    """Check if the leads file exists and its size."""
    exists = os.path.exists(DATA_FILE)
    size = os.path.getsize(DATA_FILE) if exists else 0
    return jsonify({
        "exists": exists,
        "path": os.path.abspath(DATA_FILE),
        "size": size
    })


# =====================================================
#  ENTRY POINT
# =====================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
