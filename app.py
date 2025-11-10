import csv
import io
import os
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

DATA_FILE = os.path.join("data", "leads.csv")
SCHEDULES = []  # in-memory; later you can persist to a file/DB


def load_leads_from_csv():
    """Load leads from data/leads.csv into a list of dicts."""
    leads = []
    if not os.path.exists(DATA_FILE):
        print(f"[WARN] {DATA_FILE} not found. Using empty lead list.")
        return leads

    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                amount = float(row.get("amountDue", 0) or 0)
            except ValueError:
                amount = 0.0
            distress_raw = (row.get("distressTypes") or "").strip()
            distress_list = [
                d.strip().upper()
                for d in distress_raw.split("|")
                if d.strip()
            ]

            leads.append(
                {
                    "id": row.get("id", "").strip(),
                    "parcel": row.get("parcel", "").strip(),
                    "owner": row.get("owner", "").strip(),
                    "mailingAddress": row.get("mailingAddress", "").strip(),
                    "siteAddress": row.get("siteAddress", "").strip(),
                    "zip": str(row.get("zip", "")).strip(),
                    "distressTypes": distress_list,
                    "amountDue": amount,
                    "lastUpdated": row.get("lastUpdated", "").strip(),
                }
            )
    print(f"[INFO] Loaded {len(leads)} leads from {DATA_FILE}")
    return leads


# Load once at startup (for MVP). Later you can refresh periodically if needed.
LEADS = load_leads_from_csv()


def filter_leads(leads, params):
    search = (params.get("search") or "").strip().lower()
    zip_code = (params.get("zip") or "").strip()
    min_amount = params.get("min_amount")
    max_amount = params.get("max_amount")
    distress_param = (params.get("distress") or "").strip()

    distress_filter = []
    if distress_param:
        distress_filter = [
            d.strip().upper() for d in distress_param.split(",") if d.strip()
        ]

    min_val = float(min_amount) if min_amount not in (None, "",) else None
    max_val = float(max_amount) if max_amount not in (None, "",) else None

    filtered = []
    for lead in leads:
        # search
        if search:
            haystack = " ".join(
                [
                    lead.get("owner", ""),
                    lead.get("mailingAddress", ""),
                    lead.get("siteAddress", ""),
                    lead.get("parcel", ""),
                ]
            ).lower()
            if search not in haystack:
                continue

        # zip
        if zip_code and str(lead.get("zip")) != zip_code:
            continue

        # amount range
        amt = float(lead.get("amountDue") or 0)
        if min_val is not None and amt < min_val:
            continue
        if max_val is not None and amt > max_val:
            continue

        # distress type
        lead_distress = [d.upper() for d in lead.get("distressTypes", [])]
        if distress_filter:
            if not any(d in distress_filter for d in lead_distress):
                continue

        filtered.append(lead)

    return filtered


@app.route("/")
def serve_index():
    # Serve index.html
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/leads", methods=["GET"])
def get_leads():
    filtered = filter_leads(LEADS, request.args)
    return jsonify({"items": filtered, "total": len(LEADS), "count": len(filtered)})


@app.route("/api/export", methods=["POST"])
def export_leads():
    params = request.get_json(force=True) if request.is_json else {}
    filtered = filter_leads(LEADS, params)

    # Create CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "parcel",
            "owner",
            "mailingAddress",
            "siteAddress",
            "zip",
            "distressTypes",
            "amountDue",
            "lastUpdated",
        ]
    )

    for lead in filtered:
        writer.writerow(
            [
                lead.get("id", ""),
                lead.get("parcel", ""),
                lead.get("owner", ""),
                lead.get("mailingAddress", ""),
                lead.get("siteAddress", ""),
                lead.get("zip", ""),
                "|".join(lead.get("distressTypes", [])),
                lead.get("amountDue", ""),
                lead.get("lastUpdated", ""),
            ]
        )

    csv_data = output.getvalue()
    output.close()

    filename = f"distress_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "text/csv",
    }
    return Response(csv_data, headers=headers)


@app.route("/api/schedules", methods=["POST"])
def create_schedule():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    frequency = data.get("frequency") or "weekly"
    format_ = data.get("format") or "csv"
    include_sources = data.get("includeSources") or []
    filters = data.get("filters") or {}

    if not email:
        return jsonify({"error": "Email is required"}), 400

    schedule = {
        "id": str(len(SCHEDULES) + 1),
        "email": email,
        "frequency": frequency,
        "format": format_,
        "includeSources": include_sources,
        "filters": filters,
        "createdAt": datetime.utcnow().isoformat() + "Z",
    }
    SCHEDULES.append(schedule)

    # MVP: we just store it; later a cron job can read SCHEDULES and email CSVs.
    print("[INFO] New schedule saved:", schedule)
    return jsonify({"ok": True, "schedule": schedule})


if __name__ == "__main__":
    # For local dev
    app.run(debug=True)
