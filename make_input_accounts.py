import csv, sys
from pathlib import Path

src = Path("duval_delinquent_leads_big.csv")
dst = Path("input_accounts.csv")

with src.open("r", encoding="utf-8", errors="ignore", newline="") as f:
    r = csv.DictReader(f)
    headers = r.fieldnames or []

    candidates = [
        "lienhub_account_no",
        "account",
        "account_no",
        "account_number",
        "parcel",
        "parcel_id",
        "folio",
        "folio_number",
    ]

    hmap = {h.lower(): h for h in headers}
    chosen = None
    for c in candidates:
        if c.lower() in hmap:
            chosen = hmap[c.lower()]
            break
    if not chosen:
        for h in headers:
            if "account" in h.lower():
                chosen = h
                break
    if not chosen:
        print("Could not find an account column in headers:", headers)
        sys.exit(2)

    accounts = []
    for row in r:
        v = (row.get(chosen) or "").strip()
        if v:
            accounts.append(v)

dst.write_text("account\n" + "\n".join(accounts) + "\n", encoding="utf-8")
print(f"OK: wrote {len(accounts)} accounts to {dst} using column '{chosen}'")
