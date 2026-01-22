from pathlib import Path
import csv

BASE = Path(__file__).resolve().parent

SRC = BASE / "duval_lienhub_x_live_zip_v3.csv"
OUT = BASE / "duval_delinquent_leads_big.csv"

def fnum(x):
    try:
        return float(str(x).strip().replace(",",""))
    except:
        return 0.0

def norm_bool(x):
    s = str(x).strip().upper()
    return s in {"TRUE","YES","Y","1"}

if not SRC.exists():
    raise SystemExit(f"Source file not found: {SRC.name}")

rows = []
with SRC.open(newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for r in reader:
        face = fnum(r.get("lienhub_face_amount"))
        due  = fnum(r.get("live_total_due"))
        still = norm_bool(r.get("still_delinquent"))
        # "big list" rule: include if face>0 OR due>0 OR still flagged
        if face > 0 or due > 0 or still:
            rows.append(r)

fieldnames = reader.fieldnames or (rows[0].keys() if rows else [])
OUT.write_text("", encoding="utf-8")
with OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"WROTE {len(rows)} delinquent rows -> {OUT}")
