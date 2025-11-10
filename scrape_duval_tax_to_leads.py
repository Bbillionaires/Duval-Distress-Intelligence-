import csv
import os
import re
import time
import uuid
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Base URL template – account number will be inserted
BASE_URL = "https://county-taxes.net/fl-duval/property-tax/{}"

# Input list of accounts
INPUT_ACCOUNTS = "input_accounts.csv"

# Output files used by Distress Intelligence
OUTPUT_LEADS = os.path.join("data", "leads.csv")
CACHE_CSV = os.path.join("data", "scrape_cache.csv")

HEADERS = {
    "User-Agent": "DistressIntelligenceBot/1.0 (contact: your-email@example.com)"
}

REQUEST_DELAY_SECONDS = 1.5  # be polite


def parse_amount(text):
    """Convert '$1,234.56' → 1234.56"""
    if not text:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def scrape_one_account(account):
    """
    Fetch and parse a single property-tax page.

    Returns:
      {
        "account": ...,
        "owner": ...,
        "siteAddress": ...,
        "mailingAddress": ...,
        "zip": ...,
        "years_behind": int,
        "total_due": float,
      }
    or None if error / not found.
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

    # ---------- OWNER / ADDRESS (UPDATE SELECTORS) ----------
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

    # ZIP: try to pull 5-digit ZIP from site_address
    zip_code = ""
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", site_address)
    if zip_match:
        zip_code = zip_match.group(1)

    # ---------- TAX YEAR TABLE (UPDATE SELECTORS + INDEXES) ----------
    delinquent_years = set()
    total_due = 0.0

    # Example selector; adjust to real page:
    year_rows = soup.select("table.tax-years tbody tr")  # <<< CHANGE THIS

    for tr in year_rows:
        cols = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if not cols:
            continue

        # Adjust indexes based on the real table columns
        try:
            year_text = cols[0]    # <<< CHANGE INDEX
            status_text = cols[1]  # <<< CHANGE INDEX (if there is a status)
            amount_text = cols[2]  # <<< CHANGE INDEX
        except IndexError:
            continue

        year = None
        year_match = re.search(r"\b(20\d{2})\b", year_text)
        if year_match:
            year = year_match.group(1)

        amount = parse_amount(amount_text)

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
        "last_checked": datetime.utcnow().isoformat(),
    }


# ---------- CACHE HELPERS ----------

def load_cache():
    """
    Load previous scrape results from CACHE_CSV if it exists.
    Returns dict: account -> info dict.
    """
    cache = {}
    if not os.path.exists(CACHE_CSV):
        return cache

    with open(CACHE_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            account = row.get("account", "").strip()
            if not account:
                continue
            cache[account] = {
                "account": account,
                "owner": row.get("owner", ""),
                "siteAddress": row.get("siteAddress", ""),
                "mailingAddress": row.get("mailingAddress", ""),
                "zip": row.get("zip", ""),
                "years_behind": int(row.get("years_behind") or 0),
                "total_due": float(row.get("total_due") or 0.0),
                "last_checked": row.get("last_checked", ""),
            }
    print(f"[INFO] Loaded {len(cache)} cached accounts from {CACHE_CSV}")
    return cache


def save_cache(cache):
    """Write the full cache dict back to CACHE_CSV."""
    os.makedirs(os.path.dirname(CACHE_CSV), exist_ok=True)

    fieldnames = [
        "account",
        "owner",
        "siteAddress",
        "mailingAddress",
        "zip",
        "years_behind",
        "total_due",
        "last_checked",
    ]

    with open(CACHE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for info in cache.values():
            writer.writerow(info)

    print(f"[INFO] Saved {len(cache)} accounts to {CACHE_CSV}")


# ---------- MAIN PIPELINE ----------

def build_leads_from_accounts(input_csv, out_csv):
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)

    # Load account list
    accounts = []
    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            acc = row.get("account") or row.get("Account") or row.get("acct")
            if acc:
                accounts.append(acc.strip())

    print(f"[INFO] Loaded {len(accounts)} accounts from {input_csv}")

    # Load existing cache
    cache = load_cache()

    # Scrape only accounts not in cache yet
    for i, account in enumerate(accounts, start=1):
        if account in cache:
            print(f"[INFO] Skipping {account} (already in cache)")
            continue

        info = scrape_one_account(account)
        if info:
            cache[account] = info

        print(
            f"[INFO] {i}/{len(accounts)} | acct={account} | "
            f"cached={account in cache}"
        )

        time.sleep(REQUEST_DELAY_SECONDS)

    # Save updated cache
    save_cache(cache)

    # Build leads from cache using your rules:
    #   2+ years behind OR total_due >= 10000
    leads = []
    for info in cache.values():
        if info["years_behind"] >= 2 or info["total_due"] >= 10000:
            leads.append(
                {
                    "id": str(uuid.uuid4()),
                    "parcel": info["account"],  # using account as parcel ID for now
                    "owner": info["owner"],
                    "mailingAddress": info["mailingAddress"],
                    "siteAddress": info["siteAddress"],
                    "zip": info["zip"],
                    "distressTypes": "TAX",
                    "amountDue": info["total_due"],
                    "lastUpdated": datetime.utcnow().date().isoformat(),
                }
            )

    if not leads:
        print("[WARN] No distressed leads found from cache.")
    else:
        print(f"[INFO] {len(leads)} distressed leads matched the rules.")

    # Write leads.csv for Distress Intelligence
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
        for row in leads:
            writer.writerow(row)

    print(f"[INFO] Wrote {len(leads)} leads to {out_csv}")


def main():
    build_leads_from_accounts(INPUT_ACCOUNTS, OUTPUT_LEADS)


if __name__ == "__main__":
    main()              
