from pathlib import Path
import csv

SNAPSHOT_DIR = Path("snapshots")
OUTFILE = Path("duval_delinquent_leads_latest.csv")


def load_snapshots():
    """
    Load all duval_live_zip_snapshot_*.csv files and keep the latest snapshot
    per parcel. A parcel here is identified by its parcel ID.
    """
    if not SNAPSHOT_DIR.exists():
        print(f"Snapshot dir {SNAPSHOT_DIR} does not exist.")
        return {}

    files = sorted(SNAPSHOT_DIR.glob("duval_live_zip_snapshot_*.csv"))
    if not files:
        print(f"No snapshot CSVs found in {SNAPSHOT_DIR}")
        return {}

    parcels = {}

    for fp in files:
        print(f"Reading snapshot: {fp}")
        with fp.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                parcel = (row.get("parcel") or "").strip()
                if not parcel:
                    continue

                snap_date = (row.get("snapshot_date") or "").strip()
                # Use string comparison since snapshot_date is YYYY-MM-DD
                current = parcels.get(parcel)

                # If we haven't seen this parcel or this snapshot is newer, replace
                if (current is None) or (snap_date > current.get("snapshot_date", "")):
                    parcels[parcel] = row

    print(f"Loaded latest rows for {len(parcels)} parcels")
    return parcels


def build_delinquent_leads(latest_rows):
    """
    From the latest snapshot rows, keep only parcels with total_due_numeric > 0.
    Return a list of clean lead dicts.
    """
    leads = []

    for parcel, row in latest_rows.items():
        due_str = (row.get("total_due_numeric") or "").strip()
        try:
            due_val = float(due_str) if due_str else 0.0
        except ValueError:
            due_val = 0.0

        # Only keep currently delinquent parcels
        if due_val <= 0:
            continue

        leads.append(
            {
                "parcel": parcel,
                "snapshot_date": row.get("snapshot_date", ""),
                "zip": row.get("zip", ""),
                "situs_address": row.get("situs_address", ""),
                "situs_city": row.get("situs_city", ""),
                "situs_zip": row.get("situs_zip", ""),
                "owner": row.get("owner", ""),
                "total_due_numeric": due_val,
            }
        )

    print(f"Filtered down to {len(leads)} currently delinquent parcels")
    return leads


def write_leads_csv(leads):
    if not leads:
        print("No leads to write.")
        return

    fieldnames = [
        "parcel",
        "snapshot_date",
        "zip",
        "situs_address",
        "situs_city",
        "situs_zip",
        "owner",
        "total_due_numeric",
    ]

    with OUTFILE.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in leads:
            w.writerow(row)

    print(f"Wrote {len(leads)} leads to {OUTFILE}")


def main():
    latest_rows = load_snapshots()
    if not latest_rows:
        return

    leads = build_delinquent_leads(latest_rows)
    write_leads_csv(leads)


if __name__ == "__main__":
    main()
