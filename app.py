import os
import csv
import random
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import requests

# === Flask Setup ===
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# === Config ===
DATA_FILE = os.path.join("data", "leads.csv")
SKIPTRACE_WEBHOOK_URL = os.environ.get("SKIPTRACE_WEBHOOK_URL", "")

# === Load Leads ===
def load_leads():
    leads = []
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append({
                "id": row.get("id") or str(random.randint(1000, 999999)),
                "owner": row.get("owner", "").strip(),
                "siteAddress": row.get("siteAddress", "").strip(),
                "mailingAddress": row.get("mailingAddress", "").strip(),
                "parcel": row.get("parcel", "").strip(),
                "zip": row.get("zip", "").strip(),
                "distressTypes": row.get("distressTypes", "").strip(),
                "amountDue": float(row.get("amountDue", 0) or 0)
            })
    return leads

# === Queue Assignment ===
def assign_queue(lead_id: str):
    queues = ["Alpha", "Bravo", "Charlie", "Delta"]
    # Deterministic based on id so same lead always goes to same queue
    return queues[hash(lead_id) % len(queues)]

# === ROUTES ===

# Serve homepage
@app.route("/")
def index():
    return app.send_static_file("index.html")

# Serve clean login path
@app.route("/login")
def login():
    return app.send_static_file("login.html")

# API: Get leads with filters
@app.route("/api/leads", methods=["GET"])
def api_leads():
    leads = load_leads()
    q = request.args.get("q", "").lower().strip()
    zip_filter = request.args.get("zip", "").strip()
    min_amt = float(request.args.get("min_amount", 0) or 0)
    max_amt = float(request.args.get("max_amount", 0) or 999999999)
    sources = [s.strip().lower() for s in request.args.get("sources", "").split(",") if s.strip()]

    filtered = []
    for lead in leads:
        if q and q not in (lead["owner"].lower() + lead["siteAddress"].lower() + lead["parcel"].lower()):
            continue
        if zip_filter and not lead["zip"].startswith(zip_filter):
            continue
        if not (min_amt <= lead["amountDue"] <= max_amt):
            continue
        if sources:
            match = any(src in lead["distressTypes"].lower() for src in sources)
            if not match:
                continue
        filtered.append(lead)

    return jsonify({
        "count": len(filtered),
        "total": len(leads),
        "leads": filtered
    })

# API: Export CSV
@app.route("/export")
def export_csv():
    leads = load_leads()
    q = request.args.get("q", "").lower().strip()
    zip_filter = request.args.get("zip", "").strip()
    min_amt = float(request.args.get("min_amount", 0) or 0)
    max_amt = float(request.args.get("max_amount", 0) or 999999999)
    sources = [s.strip().lower() for s in request.args.get("sources", "").split(",") if s.strip()]

    filtered = []
    for lead in leads:
        if q and q not in (lead["owner"].lower() + lead["siteAddress"].lower() + lead["parcel"].lower()):
            continue
        if zip_filter and not lead["zip"].startswith(zip_filter):
            continue
        if not (min_amt <= lead["amountDue"] <= max_amt):
            continue
        if sources:
            match = any(src in lead["distressTypes"].lower() for src in sources)
            if not match:
                continue
        filtered.append(lead)

    output_path = os.path.join("data", "export_filtered.csv")
    with open(output_path, "w", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=filtered[0].keys() if filtered else ["id"])
        writer.writeheader()
        writer.writerows(filtered)
    return send_file(output_path, as_attachment=True, download_name="distress_export.csv")

# API: Skiptrace webhook
@app.route("/api/skiptrace_order", methods=["POST"])
def skiptrace_order():
    if not SKIPTRACE_WEBHOOK_URL:
        return jsonify({"ok": False, "error": "SKIPTRACE_WEBHOOK_URL not configured"}), 500

    data = request.get_json(force=True) or {}
    lead = data.get("lead") or {}
    user = data.get("user") or "unknown"
    lead_id = lead.get("id") or str(random.randint(1000, 999999))
    queue = assign_queue(lead_id)

    payload = {
        "id": lead_id,
        "owner": lead.get("owner", ""),
        "siteAddress": lead.get("siteAddress", ""),
        "mailingAddress": lead.get("mailingAddress", ""),
        "zip": str(lead.get("zip") or ""),
        "distressTypes": lead.get("distressTypes", ""),
        "amountDue": lead.get("amountDue", 0),
        "requestedBy": user,
        "queue": queue
    }

    try:
        r = requests.post(SKIPTRACE_WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code != 200:
            return jsonify({"ok": False, "error": "Webhook failed", "status": r.status_code, "body": r.text}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "queue": queue})

# === MAIN ENTRY ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
