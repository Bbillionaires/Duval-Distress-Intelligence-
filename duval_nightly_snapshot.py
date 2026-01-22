# -*- coding: utf-8 -*-
import os
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:10000"

TARGET_ZIPS = [
    "32208","32207","32226","32209","32254",
    "32206","32234","32222","32211","32218",
    "32204","32216","32219","32244","32210",
    "32256","32205","32225","32246","32258",
    "32277","32233","32202","32224","32221",
    "32217","32220","32257","32223","32250",
    "32266","32081"
]

SNAPSHOT_DIR = Path("snapshots")
SNAPSHOT_DIR.mkdir(exist_ok=True)


def fetch_zip(z: str):
    """Call your Flask /api/search_zip endpoint for one ZIP."""
    try:
        resp = requests.get(
            f"{API_BASE}/api/search_zip",
            params={"zip": z},
            timeout=120,  # give it plenty of time
        )
        resp.raise_for_status()
        j = resp.json()
        if j.get("status") != "ok":
            print(f"ZIP {z}: API status={j.get('status')}, message={j.get('message')}")
            return []
        return j.get("rows", [])
    except Exception as e:
        print(f"ZIP {z}: ERROR {e}")
        return []


def main():
    # UTC date stamp for filename
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = SNAPSHOT_DIR / f"duval_live_zip_snapshot_{ts}.csv"
    rows = []

    for z in TARGET_ZIPS:
        print(f"Fetching zip {z} ...")
        results = fetch_zip(z)
        print(f"  -> got {len(results)} rows")
        for r in results:
            rows.append(
                {
                    "snapshot_date": ts,
                    "zip": z,
                    "parcel": r.get("parcel", ""),
                    "situs_address": r.get("situs_address", ""),
                    "situs_city": r.get("situs_city", ""),
                    "situs_zip": r.get("situs_zip", ""),
                    "owner": r.get("owner", ""),
                    "total_due_numeric": r.get("total_due_numeric", ""),
                    "bill_years": ",".join(r.get("bill_years", [])),
                }
            )
        # small pause so we don't hammer your API
        time.sleep(0.5)

    if not rows:
        print("NO ROWS (your API may have returned nothing for all zips).")
        return

    # Write CSV
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
