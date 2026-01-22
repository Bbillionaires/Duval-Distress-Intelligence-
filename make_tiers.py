import csv
from pathlib import Path

BASE = Path(__file__).resolve().parent

# Candidates you already have
CANDIDATES = [
    BASE / "auction_clean.csv",
    BASE / "auction_clean_temp.csv",
    BASE / "distress intelligence tax auction.csv",
]

OUT_T2 = BASE / "tier2_clerk_taxdeed_notices.csv"
OUT_T3 = BASE / "tier3_scheduled_tax_deed_auctions.csv"

def sniff_columns(p: Path):
    with p.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
    if not header:
        return []
    return [h.strip() for h in header]

def read_rows(p: Path):
    with p.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        dr = csv.DictReader(f)
        rows = list(dr)
    return dr.fieldnames or [], rows

def pick_file():
    usable = []
    for p in CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            cols = [c.lower() for c in sniff_columns(p)]
            usable.append((p, cols))
    return usable

def find_col(cols, keys):
    # cols = original fieldnames list
    low = [c.lower() for c in cols]
    for k in keys:
        for i, c in enumerate(low):
            if k in c:
                return cols[i]
    return None

def normalize_date(s):
    if s is None:
        return ""
    return str(s).strip()

def main():
    usable = pick_file()
    if not usable:
        print("No candidate files found.")
        return

    # Choose the best candidate for Tier 3: something with sale/auction date
    best_t3 = None
    best_score = -1
    for p, cols in usable:
        score = 0
        if any("sale" in c for c in cols): score += 3
        if any("auction" in c for c in cols): score += 3
        if any("date" in c for c in cols): score += 2
        if any("case" in c for c in cols): score += 1
        if score > best_score:
            best_score = score
            best_t3 = p

    # Choose best candidate for Tier 2: something with notice/case, may not have sale date
    best_t2 = None
    best_score2 = -1
    for p, cols in usable:
        score = 0
        if any("notice" in c for c in cols): score += 3
        if any("case" in c for c in cols): score += 3
        if any("file" in c for c in cols): score += 1
        if any("parcel" in c for c in cols) or any("account" in c for c in cols): score += 1
        if score > best_score2:
            best_score2 = score
            best_t2 = p

    print(f"Tier3 candidate: {best_t3} (score={best_score})")
    print(f"Tier2 candidate: {best_t2} (score={best_score2})")

    # Load rows
    t3_cols, t3_rows = read_rows(best_t3)
    t2_cols, t2_rows = read_rows(best_t2)

    # Column guesses
    def cols_for(rows_cols):
        parcel = find_col(rows_cols, ["parcel", "account", "folio"])
        addr   = find_col(rows_cols, ["address", "situs", "location"])
        owner  = find_col(rows_cols, ["owner"])
        case   = find_col(rows_cols, ["case", "file"])
        notice = find_col(rows_cols, ["notice", "filed", "publish"])
        sale   = find_col(rows_cols, ["sale", "auction"])
        date   = find_col(rows_cols, ["date"])  # fallback
        return parcel, addr, owner, case, notice, sale, date

    t3_parcel, t3_addr, t3_owner, t3_case, t3_notice, t3_sale, t3_date = cols_for(t3_cols)
    t2_parcel, t2_addr, t2_owner, t2_case, t2_notice, t2_sale, t2_date = cols_for(t2_cols)

    # Build Tier 3 (must have a sale/auction date-like field)
    # If file doesn’t have sale/auction column, we still export it but you’ll see blanks (means we need rescrape)
    t3_out_cols = ["parcel_or_account", "address", "owner", "case_or_file", "notice_date", "sale_or_auction_date", "source_file"]
    with OUT_T3.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=t3_out_cols)
        w.writeheader()
        for r in t3_rows:
            w.writerow({
                "parcel_or_account": (r.get(t3_parcel, "") if t3_parcel else "").strip(),
                "address": (r.get(t3_addr, "") if t3_addr else "").strip(),
                "owner": (r.get(t3_owner, "") if t3_owner else "").strip(),
                "case_or_file": (r.get(t3_case, "") if t3_case else "").strip(),
                "notice_date": normalize_date(r.get(t3_notice, "") if t3_notice else (r.get(t3_date, "") if t3_date else "")),
                "sale_or_auction_date": normalize_date(r.get(t3_sale, "") if t3_sale else ""),
                "source_file": best_t3.name,
            })

    # Build Tier 2
    t2_out_cols = ["parcel_or_account", "address", "owner", "case_or_file", "notice_date", "source_file"]
    with OUT_T2.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=t2_out_cols)
        w.writeheader()
        for r in t2_rows:
            w.writerow({
                "parcel_or_account": (r.get(t2_parcel, "") if t2_parcel else "").strip(),
                "address": (r.get(t2_addr, "") if t2_addr else "").strip(),
                "owner": (r.get(t2_owner, "") if t2_owner else "").strip(),
                "case_or_file": (r.get(t2_case, "") if t2_case else "").strip(),
                "notice_date": normalize_date(r.get(t2_notice, "") if t2_notice else (r.get(t2_date, "") if t2_date else "")),
                "source_file": best_t2.name,
            })

    print(f"WROTE: {OUT_T2}")
    print(f"WROTE: {OUT_T3}")

if __name__ == "__main__":
    main()
