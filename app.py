import csv
import os
from datetime import datetime
from typing import List, Dict, Any

import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# ===================== CONFIGURATION =====================

DATA_CSV = os.path.join("data", "leads.csv")
SKIPTRACE_WEBHOOK_URL = os.environ.get("SKIPTRACE_WEBHOOK_URL", "")

# Assign VA queues (skiptrace agents)
VA_QUEUES = ["Alpha", "Bravo", "Charlie", "Delta"]

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)


# ===================== HELPERS =====================

def load_leads() -> List[Dict[str, Any]]:
    """Load leads from data/leads.csv."""
    if not os.path.exists(DATA_CSV):
        return []

    leads: List[Dict[str, Any]] = []
    with open(DATA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["amountDue"] = float(row.get("amountDue") or 0)
            row["zip"] = str(row.get("zip") or "").strip()
            row["distressTypes"] = str(row.get("distressTypes") or "")
            leads.append(row)
    return leads


def filter_leads(
    leads: List[Dict[str, Any]],
    q: str | None,
    zip_code: str | None,
    min_amount: float | None,
    max_amount: float | None,
    sources: List[str] | None,
) -> List[Dict[str, Any]]:
    """Apply search filters."""
    results = []
    q_lower = (q or "").strip().lower()
    src_set = set([s.lower() for s in (sources or []) if s])

    for lead in leads:
        # Search filter
        if q_lower:
            text = " ".join([
                str(lead.get("owner", "")),
                str(lead.get("siteAddress", "")),
                str(lead.get("parcel", "")),
            ]).lower()
            if q_lower not in text:
                continue

        # ZIP filter
        if zip_code:
            if str(lead.get("zip") or "") != str(zip_code):
                continue

        # Amount filter
        amt = lead.get("amountDue", 0)
        if min_amount is not None and amt < min_amount:
            continue
        if max_amount is not None and amt > max_amount:
            continue

        # Source filter
        if src_set:
            lead_sources = set(
                [s.strip().lower() for s in lead.get("distressTypes", "").split("|") if s.strip()]
            )
            if not lead_sources.intersection(src_set):
                continue

        results.append(lead)

    return results


def assign_queue(lead_id: str) -> str:
    """Assigns each lead to one of the VA queues."""
    if not VA_QUEUES:
        return ""
    idx = abs(hash(lead_id)) % len(VA_QUEUES)
    return VA_QUEUES[idx]


# ===================== ROUTES =====================

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/leads", methods=["GET"])
def api_leads():
    """Returns filtered leads."""
    leads = load_leads()

    q = request.args.get("q") or ""
    zip_code = request.args.get("zip") or ""
    min_amount = float(request.args.get("min_amount") or 0)
    max_amount = float(request.args.get("max_amount") or 0)
    sources = [s for s in (request.args.get("sources") or "").split(",") if s]

    filtered = filter_leads(leads, q, zip_code, min_amount, max_amount, sources)
    return jsonify({"leads": filtered, "count": len(filtered), "total": len(leads)})


@app.route("/export", methods=["GET"])
def export_csv():
    """Exports filtered leads to CSV."""
    leads = load_leads()

    q = request.args.get("q") or ""
    zip_code = request.args.get("zip") or ""
    min_amount = float(request.args.get("min_amount") or 0)
    max_amount = float(request.args.get("max_amount") or 0)
    sources = [s for s in (request.args.get("sources") or "").split(",") if s]

    filtered = filter_leads(leads, q, zip_code, min_amount, max_amount, sources)
    tmp_path = "export_leads.csv"

    fieldnames = list(filtered[0].keys()) if filtered else [
        "id", "parcel", "owner", "mailingAddress", "siteAddress",
        "zip", "distressTypes", "amountDue", "lastUpdated"
    ]

    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in filtered:
            writer.writerow(row)

    return send_file(
        tmp_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"distress_leads_{datetime.utcnow().date()}.csv"
    )


@app.route("/api/skiptrace_order", methods=["POST"])
def skiptrace_order():
    """Sends lead info to the Google Sheets Apps Script webhook."""
    if not SKIPTRACE_WEBHOOK_URL:
        return jsonify({"ok": False, "error": "SKIPTRACE_WEBHOOK_URL not configured"}), 500

    data = request.get_json(force=True) or {}
    lead = data.get("lead") or {}
    user = data.get("user") or ""
    lead_id = lead.get("id") or ""

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead id missing"}), 400

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
        "queue": queue,
    }

    try:
        r = requests.post(SKIPTRACE_WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code != 200:
            return jsonify({
                "ok": False,
                "error": "Webhook error",
                "status": r.status_code,
                "body": r.text
            }), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "queue": queue})


# ===================== MAIN =====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
