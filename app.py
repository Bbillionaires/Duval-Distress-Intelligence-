from flask import Flask, jsonify, request
from flask_cors import CORS
import csv, os, datetime
import requests

app = Flask(__name__)
CORS(app)

# File paths
DATA_FILE = "data/leads.csv"

# ---------- UTILITIES ---------- #
def load_leads():
    """Load leads from CSV into list of dicts."""
    leads = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append(row)
    return leads


# ---------- ROUTES ---------- #
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/leads", methods=["GET"])
def get_leads():
    leads = load_leads()
    return jsonify(leads)


@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """Trigger re-fetch from GitHub raw CSV (latest scraped dataset)."""
    gh_raw = "https://raw.githubusercontent.com/Bbillionaires/Duval-Distress-Intelligence-/Azoth-made-1st/data/leads.csv"
    try:
        res = requests.get(gh_raw)
        res.raise_for_status()
        os.makedirs("data", exist_ok=True)
        with open(DATA_FILE, "wb") as f:
            f.write(res.content)
        return jsonify({"status": "success", "message": "Data refreshed from GitHub."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/skiptrace_order", methods=["POST"])
def skiptrace_order():
    """Handles user skiptrace requests and sends to Google Sheets/App Script."""
    data = request.json
    try:
        # Replace this with your deployed App Script URL
        appscript_url = "https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec"
        payload = {
            "owner": data.get("ownerName"),
            "address": data.get("mailAddress"),
            "amount_due": data.get("amountDue"),
            "user_email": data.get("userEmail"),
        }
        r = requests.post(appscript_url, data=payload)
        if r.status_code == 200:
            return jsonify({"status": "ok", "message": "Skiptrace queued."})
        else:
            return jsonify({"status": "fail", "message": f"Script error {r.text}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------- STRIPE PLACEHOLDER (for Step 5) ---------- #
@app.route("/api/create_checkout_session", methods=["POST"])
def create_checkout_session():
    """Will handle 3-day free trial subscription (to be filled in later)."""
    return jsonify({"url": "https://example.com"})


# ---------- ADMIN SUMMARY ---------- #
@app.route("/api/admin/summary", methods=["GET"])
def admin_summary():
    leads = load_leads()
    last_updated = None
    if os.path.exists(DATA_FILE):
        mtime = os.path.getmtime(DATA_FILE)
        last_updated = datetime.datetime.fromtimestamp(mtime).isoformat()
    return jsonify({
        "total_leads": len(leads),
        "last_updated": last_updated
    })


# ---------- MAIN ---------- #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
