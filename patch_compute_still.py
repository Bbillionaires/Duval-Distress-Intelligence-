import re
from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

# Find _load_delinquent_rows() and insert computed still_delinquent after each row dict is read
# We’ll inject right after: for row in reader:
needle = r"def _load_delinquent_rows\(\):"
if needle not in t:
    print("ERROR: _load_delinquent_rows not found")
    raise SystemExit(1)

# Only patch once
if "COMPUTE_STILL_DELINQUENT_FROM_AMOUNTS" in t:
    print("ALREADY PATCHED")
    raise SystemExit(0)

# Insert block after the first occurrence of "for row in reader:"
pat = re.compile(r"(def _load_delinquent_rows\(\):.*?\n)(.*?for row in reader:\n)", re.S)

m = pat.search(t)
if not m:
    print("ERROR: could not locate loop in _load_delinquent_rows")
    raise SystemExit(1)

head = m.group(1)
rest = t[m.end():]
loop_line = m.group(2)

inject = """        # COMPUTE_STILL_DELINQUENT_FROM_AMOUNTS
        # If CSV has empty/false still_delinquent, compute it from amounts
        def _to_float(x):
            try:
                return float(str(x or "0").replace(",","").strip())
            except:
                return 0.0
        face = _to_float(row.get("lienhub_face_amount"))
        due  = _to_float(row.get("live_total_due"))
        raw  = str(row.get("still_delinquent","")).strip().lower()
        raw_true = raw in ("true","yes","y","1")
        raw_false = raw in ("false","no","n","0","")
        computed = (face > 0) or (due > 0) or raw_true
        row["still_delinquent"] = True if computed else False
"""

# Put inject immediately after "for row in reader:" line
t2 = t[:m.start()] + head + loop_line + inject + rest

p.write_text(t2, encoding="utf-8")
print("PATCHED app.py: computed still_delinquent from lienhub_face_amount/live_total_due")
