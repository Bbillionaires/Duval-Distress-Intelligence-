import csv
import os
import time
import random
from dataclasses import dataclass, asdict
from typing import List, Optional

import requests

# ------------- CONFIG ------------- #

INPUT_ACCOUNTS_CSV = "input_accounts.csv"
OUTPUT_LEADS_CSV = os.path.join("data", "leads.csv")

# Distress filters
MIN_YEARS_BEHIND = 2
MIN_AMOUNT_DUE = 10000.0

# Duval / Algolia (from Chrome DevTools > Network)
ALGOLIA_APP_ID = "0LWZO52LS2"
ALGOLIA_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"  # public search key observed from site
ALGOLIA_SEARCH_URL = "https://0lwzo52ls2-dsn.algolia.net/1/indexes/*/queries"

# From your payload
ALGOLIA_INDEX_NAME = "fl-duval.property_tax"
ALGOLIA_PARAMS_SUFFIX = (
    "&hitsPerPage=15"
    "&clickAnalytics=true"
    "&facets=[]"
    "&highlightPreTag=__ais-highlight__"
    "&highlightPostTag=__/ais-highlight__"
    "&tagFilters="
)

# Polite delays
MIN_DELAY = 0.5
MAX_DELAY = 1.5

# Print the first hit once so you can confirm field names in logs
DEBUG_SHOW_FIRST_HIT = True


# ------------- DATA MODEL ------------- #

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


# ------------- HELPERS ------------- #

def read_input_accounts(path: str) -> List[dict]:
    """Read input_accounts.csv with at least an 'account' column."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found. Create it with an 'account' column.")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("account"):
                rows.append(row)
    return rows


def algolia_search_account(account: str) -> Optional[dict]:
    """
    Call Algolia index for a single account and return the first hit (dict) or None.
    Recreates the site's request: params="query=<acct>&<suffix>"
    """
    params_str = f"query={account}{ALGOLIA_PARAMS_SUFFIX}"
    payload = {"requests": [{"indexName": ALGOLIA_INDEX_NAME, "params": params_str}]}
    headers = {
        "Content-Type": "application/json",
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    }
    r = requests.post(ALGOLIA_SEARCH_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])
    if not results:
        return None
    hits = results[0].get("hits", [])
    if not hits:
        return None
    hit = hits[0]

    global DEBUG_SHOW_FIRST_HIT
    if DEBUG_SHOW_FIRST_HIT:
        print("DEBUG FIRST HIT KEYS:", list(hit.keys()))
        # print a trimmed sample to avoid huge logs
        preview = {k: hit[k] for k in list(hit.keys())[:20]}
        print("DEBUG FIRST HIT SAMPLE (trimmed):", preview)
        DEBUG_SHOW_FIRST_HIT = False

    return hit


def _first_nonempty(*vals) -> str:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _to_float(v) -> float:
    try:
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
        return float(v)
    except Exception:
        return 0.0


def _to_int(v) -> int:
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return 0


def parse_hit_to_lead(hit: dict, row: dict) -> Optional[Lead]:
    """
    Map an Algolia hit into our Lead model.
    Keys below include common variants; tweak after seeing the debug output.
    """
    owner = _first_nonempty(
        hit.get("owner"),
        hit.get("owner_name"),
        hit.get("primary_owner"),
        hit.get("name"),
    )

    site_address = _first_nonempty(
        hit.get("site_address"),
        hit.get("situs_address"),
        hit.get("property_address"),
        hit.get("address"),
    )

    mailing_address = _first_nonempty(
        hit.get("mailing_address"),
        hit.get("mail_address"),
        hit.get("mailingAddress"),
    )

    # zip from hit or fallback to input row
    zip_code = _first_nonempty(
        str(hit.get("zip", "")),
        str(hit.get("situs_zip", "")),
        str(hit.get("property_zip", "")),
        str(row.get("zip", "")),
    )

    amount_due = _to_float(
        hit.get("amount_due") or hit.get("total_due") or hit.get("balance_due") or 0
    )

    years_behind = _to_int(
        hit.get("years_behind")
        or hit.get("delinquent_years")
        or hit.get("years_delinquent")
        or 0
    )

    # Distress filter
    if years_behind < MIN_YEARS_BEHIND and amount_due < MIN_AMOUNT_DUE:
        return None

    tags = []
    if years_behind >= MIN_YEARS_BEHIND:
        tags.append(f"Tax {MIN_YEARS_BEHIND}+ yrs")
    if amount_due >= MIN_AMOUNT_DUE:
        tags.append(f"Tax ${int(MIN_AMOUNT_DUE):,}+")
    distress_types = "|".join(tags)

    parcel = _first_nonempty(
        str(row.get("parcel", "")),
        str(hit.get("parcel", "")),
        str(hit.get("parcel_id", "")),
        str(hit.get("re_account", "")),
        str(row.get("account", "")),
    )

    lead_id = f"duval_tax_{parcel or row.get('account','')}"

    return Lead(
        id=lead_id,
        owner=owner,
        siteAddress=site_address,
        mailingAddress=mailing_address,
        parcel=parcel,
        zip=zip_code,
        distressTypes=distress_types,
        amountDue=amount_due,
    )


def scrape_all_accounts(input_csv: str) -> List[Lead]:
    rows = read_input_accounts(input_csv)
    print(f"[+] Loaded {len(rows)} accounts from {input_csv}")
    leads: List[Lead] = []

    for i, row in enumerate(rows, start=1):
        account = row.get("account", "").strip()
        if not account:
            continue
        print(f"[{i}/{len(rows)}] Searching account {account} via Algolia...")
        try:
            hit = algolia_search_account(account)
            if not hit:
                print("   -> No results")
            else:
                lead = parse_hit_to_lead(hit, row)
                if lead:
                    leads.append(lead)
                    print(f"   -> ADDED {lead.owner} | ${lead.amountDue:,.2f}")
                else:
                    print("   -> Not distressed enough (filtered)")
        except Exception as e:
            print(f"   !! ERROR for account {account}: {e}")
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


def main():
    leads = scrape_all_accounts(INPUT_ACCOUNTS_CSV)
    write_leads_csv(leads, OUTPUT_LEADS_CSV)


if __name__ == "__main__":
    main()
