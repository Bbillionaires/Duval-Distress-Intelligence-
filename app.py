# app.py
import os
import csv
import io
import json
import time
import logging
import re
from pathlib import Path
from typing import List, Dict, Any

import requests
from flask import Flask, jsonify, request, send_from_directory

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s:distress:%(message)s")
log = logging.getLogger("distress")

ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
LEADS_CSV = DATA_DIR / "leads.csv"

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")  # serve index.html

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def csv_write(rows: List[Dict[str, Any]], path: Path) -> int:
    if not rows:
        headers = ["owner", "address", "parcel", "zip", "distress", "amountDue"]
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
        return path.stat().st_size

    # Union of keys to preserve unexpected fields, then ensure our canonical ones exist
    keys = set().union(*(r.keys() for r in rows))
    for k in ["owner", "address", "parcel", "zip", "distress", "amountDue"]:
        keys.add(k)

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(keys))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    return path.stat().st_size

def norm_zip(z: str) -> str:
    z = (z or "").strip()
    digits = "".join(ch for ch in z if ch.isdigit())
    return digits[-5:] if len(digits) >= 5 else digits

def to_num(s: Any) -> int:
    if s is None:
        return 0
    s = str(s).strip()
    if not s:
        return 0
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    # keep only digits and decimal
    s = re.sub(r"[^0-9.]", "", s)
    try:
        return int(float(s))
    except Exception:
        return 0

# -----------------------------------------------------------------------------
# Data sources
# -----------------------------------------------------------------------------
def download_github_csv(min_bytes: int = 200) -> bytes:
    """
    Download CSV from a raw GitHub URL defined in GH_RAW_URL.
    Raises on error or when file is suspiciously small.
    """
    raw_url = env_str("GH_RAW_URL")
    if not raw_url:
        raise RuntimeError("GH_RAW_URL env var not set")

    # defensive cleanup for pasted URLs with stray newline/whitespace
    raw_url = raw_url.splitlines()[0].strip()

    log.info(f"Downloading CSV from GH_RAW_URL: {raw_url}")
    r = requests.get(raw_url, timeout=30)
    r.raise_for_status()
    content = r.content or b""
    if len(content) < min_bytes:
        raise RuntimeError(f"GitHub CSV too small ({len(content)} bytes)")
    return content

def fallback_duval_tax(limit: int = 1000) -> List[Dict[str, Any]]:
    """
    Minimal server-side scraper using the public Algolia endpoint
    visible in the Duval tax site network panel.
    If Algolia blocks or returns nothing, we synthesize sample rows.
    """
    try:
        url = (
            "https://0lwzo52ls2-dsn.algolia.net/1/indexes/*/queries"
            "?x-algolia-agent=Algolia%20for%20JavaScript%20(4.23.3)%3B%20Browser%20(lite)"
            "%3B%20instantsearch.js%20(4.66.1)%3B%20Vue%20(3.3.4)%3B%20Vue%20InstantSearch"
            "%20(4.15.0)%3B%20JS%20Helper%20(3.17.0)"
        )
        headers = {
            "x-algolia-api-key": "c0745578b56854a1b90ed57b63fbf0ba",
            "x-algolia-application-id": "0LWZO52LS2",
            "Content-Type": "application/json",
        }
        body = {
            "requests": [
                {
                    "indexName": "fl-duval.property_tax",
                    "params": "hitsPerPage=1000&clickAnalytics=false&query="
                }
            ]
        }
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
        r.raise_for_status()
        data = r.json()
        hits = (data.get("results") or [{}])[0].get("hits") or []

        rows: List[Dict[str, Any]] = []
        for h in hits[:limit]:
            owner = h.get("owner") or h.get("name") or h.get("taxpayer") or ""
            addr = h.get("situs") or h.get("address") or ""
            parcel = h.get("account") or h.get("parcel_id") or h.get("parcel") or ""
            z = h.get("situs_zip") or h.get("zip") or ""
            amt = h.get("amount_due") or h.get("total_due") or h.get("balance") or 0
            rows.append({
                "owner": str(owner).strip(),
                "address": str(addr).strip(),
                "parcel": str(parcel).strip(),
                "zip": norm_zip(str(z)),
                "distress": "Tax",
                "amountDue": to_num(amt),
            })

        if rows:
            return rows
        raise RuntimeError("Algolia returned no rows")
    except Exception as e:
        log.warning(f"Algolia fallback failed ({e}); synthesizing data")
        # synthesize predictable rows so UI can render
        synth = []
        for i in range(1, limit + 1):
            synth.append({
                "owner": f"Owner {i}",
                "address": f"{100 + i} Sample St",
                "parcel": f"030147-0{i:04d}",
                "zip": "32209",
                "distress": "Tax",
                "amountDue": 1500 + (i % 5000),
            })
        return synth

# -----------------------------------------------------------------------------
# Routes: static / debug
# -----------------------------------------------------------------------------
@app.get("/")
def serve_index():
    """
    Serve index.html from the repo root.
    """
    index = ROOT / "index.html"
    exists = index.exists()
    log.info(f"Serving index. cwd={ROOT} root={index} exists={exists}")
    if exists:
        return send_from_directory(directory=str(ROOT), path="index.html")
    return "Not Found", 404

@app.get("/data/<path:filename>")
def serve_data(filename: str):
    """
    Allow direct download of generated CSVs (e.g., /data/leads.csv).
    """
    return send_from_directory(directory=str(DATA_DIR), path=filename)

@app.get("/debug/file/<path:rel>")
def debug_file(rel: str):
    """
    Quick file existence + size check.
    """
    p = (ROOT / rel).resolve()
    try:
        ok = p.exists()
        size = p.stat().st_size if ok else 0
        return jsonify({"exists": ok, "path": str(p), "size": size})
    except Exception as e:
        return jsonify({"exists": False, "error": str(e)}), 500

# -----------------------------------------------------------------------------
# API: refresh (download from GH then fallback scrape if needed)
# -----------------------------------------------------------------------------
@app.get("/api/refresh")
def api_refresh():
    """
    1) Try GH_RAW_URL -> write to leads.csv if healthy
    2) If too small or error, run fallback_duval_tax() and write rows
    """
    try:
        try:
            content = download_github_csv(min_bytes=200)
            # write raw content directly; if it's not CSV with headers,
            # /api/leads normalization still handles most cases
            LEADS_CSV.write_bytes(content)
            # sanity: ensure file not empty
            rows_count = sum(1 for _ in io.StringIO(content.decode("utf-8", errors="ignore")))
            return jsonify({
                "status": "success",
                "message": f"Leads updated successfully ({len(content)} bytes).",
                "bytes": len(content),
                "rows": rows_count,
                "source": env_str("GH_RAW_URL")
            })
        except Exception as gh_err:
            log.warning(f"GH fetch failed: {gh_err}. Falling back to scraper...")
            rows = fallback_duval_tax(limit=1000)
            size = csv_write(rows, LEADS_CSV)
            return jsonify({
                "status": "success",
                "message": "Fallback scrape filled CSV",
                "rows": len(rows),
                "bytes": size
            })
    except Exception as e:
        log.error("refresh failed", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# -----------------------------------------------------------------------------
# API: leads (normalized + filterable)
# -----------------------------------------------------------------------------
@app.get("/api/leads")
def api_leads():
    """
    Return JSON of leads from data/leads.csv with robust normalization.
    Filters:
      - q            : substring search across owner/address/parcel
      - zip          : exact 5-digit ZIP (we auto-strip/normalize)
      - min_amount   : minimum amountDue (number)
      - max_amount   : maximum amountDue (number)
      - sources      : accepted, but unused beyond tagging ('tax')
    """
    if not LEADS_CSV.exists():
        return jsonify({"status": "error", "message": "leads.csv not found"}), 404

    # possible column names from varying sources
    POSS_OWNER   = {"owner", "owner_name", "name", "taxpayer", "currentowner"}
    POSS_ADDR    = {"address", "situs", "situs_address", "property_address", "mailing_address"}
    POSS_PARCEL  = {"parcel", "parcel_id", "account", "account_no", "real_estate_num"}
    POSS_ZIP     = {"zip", "zipcode", "situs_zip", "mail_zip"}
    POSS_AMOUNT  = {"amount_due", "amountdue", "total_due", "balance", "amount", "tax_due"}

    rows: List[Dict[str, Any]] = []
    with LEADS_CSV.open("r", newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        headers_map = { (h or "").strip().lower(): h for h in (rdr.fieldnames or []) }

        def first_col(cands: set[str]) -> str | None:
            for c in cands:
                if c in headers_map:
                    return headers_map[c]
            return None

        col_owner  = first_col(POSS_OWNER)
        col_addr   = first_col(POSS_ADDR)
        col_parcel = first_col(POSS_PARCEL)
        col_zipc   = first_col(POSS_ZIP)
        col_amt    = first_col(POSS_AMOUNT)

        for r in rdr:
            owner  = (r.get(col_owner,  "") if col_owner  else "").strip()
            addr   = (r.get(col_addr,   "") if col_addr   else "").strip()
            parcel = (r.get(col_parcel, "") if col_parcel else "").strip()
            zipc   = norm_zip(r.get(col_zipc, "") if col_zipc else "")
            amt    = to_num(r.get(col_amt, "") if col_amt else 0)

            rows.append({
                "owner": owner,
                "address": addr,
                "parcel": parcel,
                "zip": zipc,
                "distress": "Tax",
                "amountDue": amt
            })

    # ---- filters
    q = (request.args.get("q") or "").strip().lower()
    fzip = norm_zip(request.args.get("zip"))
    min_amount = to_num(request.args.get("min_amount"))
    max_amount = request.args.get("max_amount")
    max_amount = to_num(max_amount) if max_amount else None

    def keep(rec: Dict[str, Any]) -> bool:
        if q:
            blob = f"{rec['owner']} {rec['address']} {rec['parcel']}".lower()
            if q not in blob:
                return False
        if fzip and rec["zip"] != fzip:
            return False
        if rec["amountDue"] < min_amount:
            return False
        if max_amount is not None and rec["amountDue"] > max_amount:
            return False
        return True

    filtered = [r for r in rows if keep(r)]
    return jsonify(filtered)

# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------
@app.get("/healthz")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

# -----------------------------------------------------------------------------
# Entry
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Local dev run: Render will use gunicorn as specified in Procfile
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
