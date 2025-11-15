import os
import csv
import time
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

CSV_FILE = "duval_leads.csv"
REFRESH_SECONDS = 30 * 24 * 60 * 60   # 30 days

app = Flask(__name__)


# ----------------------------------------------
# Load CSV into dictionary
# ----------------------------------------------
def load_csv():
    if not os.path.exists(CSV_FILE):
        return {}

    leads = {}
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads[row["parcel"]] = row
    return leads


# ----------------------------------------------
# Save dictionary back to CSV
# ----------------------------------------------
def save_csv(leads):
    fieldnames = [
        "parcel",
        "owner",
        "mailing_address",
        "property_address",
        "assessed_value",
        "tax_years_json",
        "total_due",
        "last_updated"
    ]

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for parcel, data in leads.items():
            writer.writerow(data)


# ----------------------------------------------
# Scrape live Duval Tax Collector
# ----------------------------------------------
def scrape_duval(parcel):
    try:
        search_url = "https://tc.coj.net/RealEstate/Search"
        session = requests.Session()

        # GET page
        r1 = session.get(search_url, timeout=10)
        soup1 = BeautifulSoup(r1.text, "html.parser")

        # Token
        token_tag = soup1.find("input", {"name": "__RequestVerificationToken"})
        token = token_tag["value"] if token_tag else ""

        # POST search
        payload = {
            "__RequestVerificationToken": token,
            "Parcel": parcel
        }

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": search_url
        }

        r2 = session.post(search_url, data=payload, headers=headers, timeout=15)
        soup2 = BeautifulSoup(r2.text, "html.parser")

        # -------- Extract fields --------
        def extract(id):
            tag = soup2.find("span", id=id)
            return tag.text.strip() if tag else "N/A"

        owner = extract("OwnerName")
        mailing = extract("MailingAddress")
        prop_addr = extract("SitusAddress")
        assessed = extract("AssessedValue")

        # Tax Years
        table = soup2.find("table", id="TaxesTable")
        years = []
        if table:
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 5:
                    years.append({
                        "year": cols[0].text.strip(),
                        "type": cols[1].text.strip(),
                        "amount_due": cols[4].text.strip()
                    })

        # Total due
        total_due = 0
        for y in years:
            try:
                total_due += float(y["amount_due"].replace("$", "").replace(",", ""))
            except:
                pass

        return {
            "parcel": parcel,
            "owner": owner,
            "mailing_address": mailing,
            "property_address": prop_addr,
            "assessed_value": assessed,
            "tax_years_json": str(years),
            "total_due": f"${total_due:,.2f}",
            "last_updated": str(int(time.time())),
            "status": "success"
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "parcel": parcel}


# ----------------------------------------------
# API: /api/parcel?parcel=0862860000
# ----------------------------------------------
@app.route("/api/parcel", methods=["GET"])
def parcel_lookup():
    parcel = request.args.get("parcel")
    if not parcel:
        return jsonify({"error": "Missing ?parcel="}), 400

    leads = load_csv()

    # If exists + not expired (fresh under 30 days)
    if parcel in leads:
        age = time.time() - float(leads[parcel]["last_updated"])
        if age < REFRESH_SECONDS:
            return jsonify({"source": "csv_cache", **leads[parcel]})

    # Otherwise → scrape live
    result = scrape_duval(parcel)

    if result["status"] == "success":
        leads[parcel] = result
        save_csv(leads)
        return jsonify({"source": "live_scrape", **result})

    return jsonify(result)


@app.route("/")
def home():
    return "Hybrid Distress Intelligence backend is online."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
