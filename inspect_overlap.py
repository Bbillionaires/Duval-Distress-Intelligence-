import csv
from pathlib import Path

SNAP_DIR = Path("snapshots")
AUCTION_CSV = Path("distress intelligence tax auction.csv")

def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits

def main():
    files = sorted(SNAP_DIR.glob("duval_live_zip_snapshot_*.csv"))
    if not files:
        print("No snapshot files in ./snapshots")
        return

    live_file = files[-1]
    print(f"Using live snapshot: {live_file}")

    live_keys = set()
    with live_file.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            k = normalize(row.get("parcel", ""))
            if k:
                live_keys.add(k)
    print(f"Distinct live parcel keys: {len(live_keys)}")

    auction_keys = set()
    if not AUCTION_CSV.exists():
        print(f"Auction CSV not found: {AUCTION_CSV}")
        return

    with AUCTION_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            k = normalize(row.get("Parcel ID", ""))
            if k:
                auction_keys.add(k)
    print(f"Distinct auction parcel keys: {len(auction_keys)}")

    both = live_keys & auction_keys
    print(f"Intersection size (live n auction): {len(both)}")
    for k in list(both)[:10]:
        print("  common parcel:", k)

if __name__ == "__main__":
    main()
