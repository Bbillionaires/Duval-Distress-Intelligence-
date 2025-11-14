import os
import csv
import requests
from datetime import datetime, timedelta

# ==============================
# CONFIG
# ==============================

# CSV file used by your app; change this if your file is named differently
CSV_FILE = os.environ.get("CSV_FILE", "duval_data.csv")

# How many leads to refresh per run (safe cap)
MAX_REFRESH_PER_RUN = 200  # you can lower to 100 if you want to be extra safe

# How "old" a lead must be (in days) before we re-check it
REFRESH_AGE_DAYS = 30

# Duval's Algolia (SOURCE OF TRUTH for tax status)
COUNTY_ALG_APP_ID = "0LWZO52LS2"
COUNTY_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
COUNTY_ALG_INDEX = "fl-duval.property_tax"

# ==============================
# CSV Helpers
# ==============================

def load_rows():
    if not os.path.exists(CSV_FILE):
        print(f"[INFO] CSV file not found: {CSV_FILE}")
        return []

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return rows, reader.fieldnames


def save_rows(rows, fieldnames):
    # ensure our extra fields are present
    extra_fields = ["status", "last_checked_at", "last_amount_due", "last_distress_status"]
    for ef in extra_fields:
        if ef not in fieldnames:
            fieldnames.append(ef)

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            # fill missing fields with empty string
            for ef in fieldnames:
                r.setdefault(ef, "")
            writer.writerow(r)


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except ValueError:
        return None


def parse_amount(value):
    if value is None:
        return 0.0
    s = str(value).replace("$", "").replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

# ==============================
# Duval Algolia Lookup
# ==============================

def check_duval_tax(parcel):
    """
    Check live tax status for a single parcel via Duval's Algolia.
    Returns a dict with amount_due and maybe other info, or None.
    """
    if not parcel:
        return None

    url = f"https://{COUNTY_ALG_APP_ID}-dsn.algolia.net/1/indexes/{COUNTY_ALG_INDEX}/query"
    headers = {
        "X-Algolia-Application-Id": COUNTY_ALG_APP_ID,
        "X-Algolia-API-Key": COUNTY_ALG_API_KEY,
        "Content-Type": "application/json",
    }

    params_str = f"query={parcel}&hitsPerPage=1"

    try:
        resp = requests.post(url, headers=headers, json={"params": params_str}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            return None

        hit = hits[0]

        amount_due = hit.get("amount_due") or hit.get("amountDue") or 0
        owner = hit.get("owner_name") or hit.get("owner") or ""
        address = hit.get("situs_address") or hit.get("address") or ""
        zip_code = str(hit.get("zip", "") or "")

        return {
            "amount_due": parse_amount(amount_due),
            "owner": owner,
            "address": address,
            "zip": zip_code,
        }
    except Exception as e:
        print(f"[WARN] Failed checking parcel {parcel}: {e}")
        return None

# ==============================
# Refresh Logic
# ==============================

def should_refresh(row, today):
    """
    Decide if this row should be refreshed this run:
    - status in ('', 'new', 'working') by default,
    - last_checked_at is missing or older than REFRESH_AGE_DAYS.
    """
    status = (row.get("status") or "").lower()
    if status and status not in ("new", "working"):
        return False

    last_checked_str = row.get("last_checked_at") or ""
    last_date = parse_date(last_checked_str)
    if last_date is None:
        return True  # never checked before

    if today - last_date >= timedelta(days=REFRESH_AGE_DAYS):
        return True

    return False


def main():
    today = datetime.utcnow().date()

    rows, fieldnames = load_rows()
    if not rows:
        print("[INFO] No rows to refresh.")
        return

    # Pick candidates
    candidates = []
    for idx, row in enumerate(rows):
        if should_refresh(row, today):
            candidates.append((idx, row))

    if not candidates:
        print("[INFO] No leads are due for refresh (all checked within last 30 days).")
        return

    print(f"[INFO] Found {len(candidates)} leads due for refresh.")
    to_process = candidates[:MAX_REFRESH_PER_RUN]
    print(f"[INFO] Will refresh up to {len(to_process)} leads this run.")

    refreshed_count = 0
    still_delinquent = 0
    now_clean = 0
    not_found = 0

    for idx, row in to_process:
        parcel = row.get("parcel") or row.get("account") or ""
        parcel = parcel.strip()
        if not parcel:
            print(f"[SKIP] Row {idx} missing parcel/account.")
            continue

        info = check_duval_tax(parcel)

        if info is None:
            # Could not find or error
            row["last_distress_status"] = "unknown"
            row["last_checked_at"] = today.isoformat()
            # status unchanged
            rows[idx] = row
            not_found += 1
            refreshed_count += 1
            continue

        amount_due = info["amount_due"]
        row["last_amount_due"] = str(amount_due)

        # Update basic fields if we got fresher data
        if info["owner"]:
            row["owner"] = info["owner"]
        if info["address"]:
            row["address"] = info["address"]
        if info["zip"]:
            row["zip"] = info["zip"]

        if amount_due > 0:
            row["last_distress_status"] = "tax_delinquent"
            # keep status as 'new' or 'working' if already set
            if not row.get("status"):
                row["status"] = "new"
            still_delinquent += 1
        else:
            row["last_distress_status"] = "paid"
            row["status"] = "paid"
            now_clean += 1

        row["last_checked_at"] = today.isoformat()
        rows[idx] = row
        refreshed_count += 1

    save_rows(rows, fieldnames)

    print(f"[DONE] Refreshed {refreshed_count} leads.")
    print(f"        Still delinquent: {still_delinquent}")
    print(f"        Now paid/clean:  {now_clean}")
    print(f"        Not found/unknown: {not_found}")


if __name__ == "__main__":
    main()
