import csv
import os
import time
import random
from dataclasses import dataclass, asdict
from typing import List

import requests
from bs4 import BeautifulSoup

# ------------- CONFIG ------------- #

INPUT_ACCOUNTS_CSV = "input_accounts.csv"
OUTPUT_LEADS_CSV = os.path.join("data", "leads.csv")

# ✅ You MUST update this template to match the real Duval site URL pattern.
# Example pattern – adjust to the real one:
PROPERTY_URL_TEMPLATE = "https://county-taxes.net/fl-duval/property-tax/search?account={account}"

# polite delay between requests (seconds)
MIN_DELAY = 1.5
MAX_DELAY = 3.5

# filter rules
MIN_YEARS_BEHIND = 2
MIN_AMOUNT_DUE = 10000.0


# ------------- MODELS ------------- #

@dataclass
class Lead:
    id: str
    owner: str
    siteAddress: str
    mailingAddress: str
    parcel: str
    zip: str
    distressTypes: str
    amountDue: float


# ------------- CORE SCRAPER ------------- #

def read_input_accounts(path: str) -> List[dict]:
    """
    Expect a CSV with at least a column 'account' and optional 'parcel', 'zip'.
    Example:
        account,parcel,zip
        1234567890,12345-0000,32209
    """
    rows = []
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found. Create it with an 'account' column.")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("account"):
                rows.append(row)
    return rows


def fetch_property_html(account: str) -> str:
    """Fetch raw HTML for a single account."""
    url = PROPERTY_URL_TEMPLATE.format(account=account)
    print(f"[+] Fetching {url}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_property_page(html: str, account_row: dict) -> Lead | None:
    """
    Parse Duval property tax page HTML and return a Lead object
    IF it meets our distress criteria (2+ years or $10k+ due).

    ⚠️ YOU MUST UPDATE THE SELECTORS BELOW to match the live site.
    Use browser dev tools (Inspect Element) to find the right IDs/classes.
    """
    soup = BeautifulSoup(html, "lxml")

    # --------- EXAMPLE SELECTORS (PLACEHOLDER!) ---------
    # Owner name
    owner_el = soup.select_one(".owner-name")  # update selector
    owner = owner_el.get_text(strip=True) if owner_el else ""

    # Site / property address
    site_el = soup.select_one(".property-address")  # update selector
    site_address = site_el.get_text(strip=True) if site_el else ""

    # Mailing address
    mail_el = soup.select_one(".mailing-address")  # update selector
    mailing_address = mail_el.get_text(" ", strip=True) if mail_el else ""

    # Zip – either from the page or from the CSV input
    zip_code = account_row.get("zip", "").strip()
    if not zip_code and site_address:
        # crude zip guess from last 5 digits in line
        parts = site_address.split()
        if parts and parts[-1].isdigit() and len(parts[-1]) == 5:
            zip_code = parts[-1]

    # Amount due – you’ll likely need to adjust the selector / parsing
    amount_el = soup.select_one(".amount-due")  # update selector
    amount_due = 0.0
    if amount_el:
        text = amount_el.get_text(strip=True).replace("$", "").replace(",", "")
        try:
            amount_due = float(text)
        except ValueError:
            amount_due = 0.0

    # Years behind – maybe a table of years; this is just an example
    years_behind = 0
    year_rows = soup.select(".delinquent-year-row")  # update selector
    years_behind = len(year_rows)

    # CHECK DISTRESS RULES
    if years_behind < MIN_YEARS_BEHIND and amount_due < MIN_AMOUNT_DUE:
        # doesn’t meet our filter – skip
        return None

    # Distress tags (you can expand this later with other data sources)
    distress_tags = []
    if years_behind >= MIN_YEARS_BEHIND:
        distress_tags.append("Tax 2+ yrs")
    if amount_due >= MIN_AMOUNT_DUE:
        distress_tags.append(f"Tax ${MIN_AMOUNT_DUE:,.0f}+")

    distress_str = "|".join(distress_tags)

    parcel = account_row.get("parcel", "").strip()
    if not parcel:
        parcel = account_row.get("account", "").strip()

    lead_id = f"duval_tax_{parcel or account_row.get('account','')}"

    return Lead(
        id=lead_id,
        owner=owner,
        siteAddress=site_address,
        mailingAddress=mailing_address,
        parcel=parcel,
        zip=zip_code,
        distressTypes=distress_str,
        amountDue=amount_due,
    )


def scrape_all_accounts(input_csv: str) -> List[Lead]:
    accounts = read_input_accounts(input_csv)
    print(f"[+] Loaded {len(accounts)} accounts from {input_csv}")

    leads: List[Lead] = []

    for i, row in enumerate(accounts, start=1):
        account = row.get("account")
        if not account:
            continue

        try:
            html = fetch_property_html(account)
            lead = parse_property_page(html, row)
            if lead:
                leads.append(lead)
                print(f"    -> ADDED lead for {account}: {lead.owner} | ${lead.amountDue:,.2f}")
            else:
                print(f"    -> SKIP {account}: not distressed enough")
        except Exception as e:
            print(f"    !! ERROR for account {account}: {e}")

        # polite random delay
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    print(f"[+] Finished. Collected {len(leads)} distressed leads.")
    return leads


def write_leads_csv(leads: List[Lead], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "owner",
                "siteAddress",
                "mailingAddress",
                "parcel",
                "zip",
                "distressTypes",
                "amountDue",
            ],
        )
        writer.writeheader()
        for lead in leads:
            writer.writerow(asdict(lead))
    print(f"[+] Wrote {len(leads)} leads to {output_path}")


# ------------- CLI ENTRY ------------- #

def main():
    leads = scrape_all_accounts(INPUT_ACCOUNTS_CSV)
    write_leads_csv(leads, OUTPUT_LEADS_CSV)


if __name__ == "__main__":
    main()
