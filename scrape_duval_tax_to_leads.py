import csv
import os
import re
import time
import uuid
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://county-taxes.net/fl-duval/property-tax/{}"  # account number goes in {}
INPUT_ACCOUNTS = "input_accounts.csv"  # your source list of accounts
OUTPUT_LEADS = os.path.join("data", "leads.csv")

HEADERS = {
    "User-Agent": "DistressIntelligenceBot/1.0 (contact: your-email@example.com)"
}

REQUEST_DELAY_SECONDS = 1.5  # be polite: 1–2 seconds between requests


def parse_amount(text):
    """
    Convert strings like '$1,234.56' or '1,234.56' into float.
    Returns 0.0 on error.
    """
    if not text:
        return 0.0
    # remove $ and commas
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def scrape_one_account(account):
    """
    Fetch and parse a single property-tax page.
    Returns a dict with:
      {
        "account": ...,
        "owner": ...,
        "siteAddress": ...,
        "mailingAddress": ...,
        "zip": ...,
        "years_behind": int,
        "total_due": float,
      }
    or None if the page is missing / invalid.
    """
    url = BASE_URL.format(account)
    print(f"[INFO] Fetching {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        print(f"[WARN] Request failed for account {account}: {e}")
        return None

    if resp.status_code == 404:
        print(f"[INFO] Account {account} not found (404).")
        return None
    if resp.status_code != 200:
        print(f"[WARN] HTTP {resp.status_code} for account {account}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # ---- OWNER / ADDRESS PARSING (YOU MUST ADJUST SELECTORS) ----
    # Example placeholders:
    owner = ""
    owner_el = soup.select_one(".owner-name")  # <<< CHANGE THIS
    if owner_el:
        owner = owner_el.get_text(strip=True)

    site_address = ""
    addr_el = soup.select_one(".property-address")  # <<< CHANGE THIS
    if addr_el:
        site_address = addr_el.get_text(" ", strip=True)

    mailing_address = ""
    mail_el = soup.select_one(".mailing-address")  # <<< CHANGE THIS
    if mail_el:
        mailing_address = mail_el.get_text(" ", strip=True)

    # Derive ZIP from site_address if not separately provided
    zip_code = ""
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", site_address)
    if zip_match:
        zip_code = zip_match.group(1)

    # ---- TAX YEAR TABLE PARSING (YOU MUST ADJUST SELECTORS) ----
    delinquent_years = set()
    total_due = 0.0

    # Example: table with each tax year row
    # Inspect the page to find the right selector.
    # Maybe something like: table.tax-years tbody tr
    year_rows = soup.select("table.tax-years tbody tr")  # <<< CHANGE THIS

    for tr in year_rows:
        cols = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not cols:
            continue

        # You must adjust these indexes based on real table columns
        # Example layout:
        #   [year, status, amount_due, something_else]
        try:
            year_text = cols[0]   # <<< CHANGE INDEX
            status_text = cols[1] # <<< CHANGE INDEX (if there is a status)
            amount_text = cols[2] # <<< CHANGE INDEX
        except IndexError:
            continue

        year = None
        year_match = re.search(r"\b(20\d{2})\b", year_text)
        if year_match:
            year = year_match.group(1)

        amount = parse_amount(amount_text)

        # Decide what counts as delinquent:
        #   - positive amount due
        #   - or status contains "Delinquent" / "Unpaid"
        is_delinquent = False
        if amount > 0:
            is_delinquent = True
        if "delinquent" in status_text.lower() or "unpaid" in status_text.lower():
            is_delinquent = True

        if year and is_delinquent:
            delinquent_years.add(year)
            total_due += amount

    years_behind = len(delinquent_years)

    return {
        "account": account,
        "owner": owner,
        "siteAddress": site_address,
        "mailingAddress": mailing_address,
        "zip": zip_code,
        "years_behind": years_behind,
        "total_due": round(total_due, 2),
    }


def build_leads_from_accounts(input_csv, out_csv):
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)

    # 1) read accounts (and optional zip) from CSV
    accounts = []
    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            acc = row.get("account") or row.get("Account") or row.get("acct")
            if acc:
                accounts.append(acc.strip())

    print(f"[INFO] Loaded {len(accounts)} accounts from {input_csv}")

    leads = []

    for i, account in enumerate(accounts, start=1):
        info = scrape_one_account(account)
        if not info:
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        # Apply your rules: 2+ years behind OR > $10,000 due
        if info["years_behind"] >= 2 or info["total_due"] >= 10000:
            lead = {
                "id": str(uuid.uuid4()),
                "parcel": info["account"],  # you can swap to real parcel later
                "owner": info["owner"],
                "mailingAddress": info["mailingAddress"],
                "siteAddress": info["siteAddress"],
                "zip": info["zip"],
                "distressTypes": "TAX",
                "amountDue": info["total_due"],
                "lastUpdated": datetime.utcnow().date().isoformat(),
            }
            leads.append(lead)

        print(
            f"[INFO] {i}/{len(accounts)} | acct={account} | years={info['years_behind']} | "
            f"due={info['total_due']} | kept={info['years_behind'] >= 2 or info['total_due'] >= 10000}"
        )

        time.sleep(REQUEST_DELAY_SECONDS)

    if not leads:
        print("[WARN] No leads passed the filters.")
        return

    # Write leads to data/leads.csv for Distress Intelligence
    fieldnames = [
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

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leads)

    print(f"[INFO] Wrote {len(leads)} leads to {out_csv}")


def main():
    build_leads_from_accounts(INPUT_ACCOUNTS, OUTPUT_LEADS)


if __name__ == "__main__":
    main()
