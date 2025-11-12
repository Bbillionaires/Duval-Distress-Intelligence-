import os
import csv
import datetime
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import requests

# Serve static files from the repo root
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ---- Config ----
DATA_FILE = "data/leads.csv"

# Optional: override via Render env var GH_RAW_URL
GH_RAW_URL = os.environ.get(
    "GH_RAW_URL",
    # fallback to your repo/branch raw path for leads.csv
    "https://raw.githubusercontent.com/Bbillionaires/Duval-Distress-Intelligence-/Azoth-made-1st/data/leads.csv",
)

# ---- Static / SPA routes ----
@app.route("/")
def root():
    # serve index.html from repo root
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:path>")
def any_path(path):
    # if a real file, serve it; otherwise SPA fallback to index.html
    full = os.path.join(app.static_folder, path)
    if os.path.isfile(full):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

# ---- Health ----
@app.route("/api/health")
def health():
    return jsonify({"ok": True})

# ---- Data helpers ----
def load_leads():
    items = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                items.append(row)
    return items

# ---- API: read leads ----
@app.route("/api/leads", methods=["GET"])
def api_leads():
    return jsonify(load_leads())

# ---- API: refresh from GitHub raw ----
@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        res = requests.get(GH_RAW_URL, timeout=30)
        res.raise_for_status()
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "wb") as f:
            f.write(res.content)
        return jsonify({"status": "success", "message": "Leads updated from GitHub."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ---- API: skiptrace placeholder ----
@app.route("/api/skiptrace_order", methods=["POST"])
def api_skiptrace():
    data = request.json or {}
    # plug in your App Script URL when ready
    appscript_url = os.environ.get("GAS_URL", "")
    if not appscript_url:
        return jsonify({"status": "error", "message": "App Script URL not set"}), 400
    try:
        payload = {
            "owner": data.get("ownerName"),
            "address": data.get("mailAddress"),
            "amount_due": data.get("amountDue"),
            "user_email": data.get("userEmail"),
        }
        r = requests.post(appscript_url, data=payload, timeout=20)
        r.raise_for_status()
        return jsonify({"status": "ok", "message": "Skiptrace queued"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ---- Admin summary ----
@app.route("/api/admin/summary", methods=["GET"])
def admin_summary():
    leads = load_leads()
    last_updated = None
    if os.path.exists(DATA_FILE):
        mtime = os.path.getmtime(DATA_FILE)
        last_updated = datetime.datetime.fromtimestamp(mtime).isoformat()
    return jsonify({"total_leads": len(leads), "last_updated": last_updated})

# ---- Entrypoint ----
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
