import csv
from pathlib import Path
import glob

SNAP_DIR = Path("snapshots")
AUCTION_CSV = Path("distress intelligence tax auction.csv")

def normalize_parcel(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits

def load_live():
    files = sorted(SNAP_DIR.glob("duval_live_zip_snapshot_*.csv"))
    if not files:
        print("No live snapshots found in ./snapshots")
        return {}
    latest = files[-1]
    print(f"Using live snapshot: {latest}")
    lookup = {}
    with latest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = normalize_parcel(row.get("parcel", ""))
            if key and key not in lookup:
                lookup[key] = row
    print(f"Loaded {len(lookup)} distinct live parcels")
    return lookup

def load_auction():
    rows = []
    if not AUCTION_CSV.exists():
        print(f"Auction CSV not found: {AUCTION_CSV}")
        return rows
    with AUCTION_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["parcel_digits"] = normalize_parcel(row.get("Parcel ID", ""))
            rows.append(row)
    print(f"Loaded {len(rows)} auction rows")
    return rows

def main():
    live = load_live()
    auction = load_auction()

    if not live or not auction:
        print("Nothing to join (live or auction missing)")
        return

    joined = []
    for a in auction:
        key = a.get("parcel_digits", "")
        if not key:
            continue
        m = live.get(key)
        if not m:
            continue

        joined.append({
            "parcel_digits": key,
            "auction_parcel_id": a.get("Parcel ID", ""),
            "auction_sale_date": a.get("Sale Date", ""),
            "auction_status": a.get("Status", ""),
            "auction_opening_bid": a.get("Opening Bid", ""),
            "auction_address": a.get("Address", ""),
            "auction_city": a.get("City", ""),
            "auction_zip": a.get("Zip", ""),
            "live_parcel": m.get("parcel", ""),
            "live_owner": m.get("owner", ""),
            "live_situs_address": m.get("situs_address", ""),
            "live_situs_city": m.get("situs_city", ""),
            "live_situs_zip": m.get("situs_zip", ""),
            "live_zip": m.get("zip", ""),
            "live_total_due_numeric": m.get("total_due_numeric", ""),
        })

    print(f"Joined rows (auction n live): {len(joined)}")
    out_path = Path("duval_auction_x_live.csv")
    if joined:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(joined[0].keys()))
            writer.writeheader()
            writer.writerows(joined)
        print(f"Wrote {len(joined)} rows to {out_path}")
    else:
        print("No matches found; nothing written")

if __name__ == "__main__":
    main()
