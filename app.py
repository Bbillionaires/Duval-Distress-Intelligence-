import csv
import io
import json
import os
import pathlib
from datetime import datetime
from typing import Dict, List, Tuple

import requests
from flask import Flask, jsonify, request, send_file, Response

# --------------------------
# Config & constants
# --------------------------
ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "leads.csv"

# You can set these in Render → Environment; we also default to the values you captured
ALG_APP_ID = os.getenv("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALG_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX = os.getenv("ALG_INDEX", "fl-duval.property_tax")

# Optional GitHub raw CSV fallback (must point to a CSV; used if Algolia fails)
GH_RAW_URL = os.getenv("GH_RAW_URL", "").strip()

# Default headers/shape we keep on disk and return to the UI
CSV_HEADERS = ["owner", "address", "parcel", "zip", "distress", "amountDue"]

# Flask app
app = Flask(__name__)

# --------------------------
# Helpers
# --------------------------
def _ensure_csv(path: pathlib.Path = CSV_PATH):
    """Create CSV with headers if missing."""
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()

def _write_rows(rows: List[Dict], path: pathlib.Path = CSV_PATH) -> Tuple[int, int]:
    """Write/replace rows into CSV; returns (#rows, #bytes)."""
    _ensure_csv(path)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in CSV_HEADERS})
    data = buf.getvalue()
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(data)
    return len(rows), len(data.encode("utf-8"))

def _read_csv(path: pathlib.Path = CSV_PATH) -> List[Dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)

def _norm(s):
    return (s or "").strip()

def _algolia_endpoint() -> str:
    # Use -dsn subdomain for distributed search
    return f"https://{ALG_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

def _algolia_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Algolia-Application-Id": ALG_APP_ID,
        "X-Algolia-API-Key": ALG_API_KEY,
    }

def _extract_amount(hit: Dict) -> str:
    """
    Algolia documents can use different keys for due amounts.
    We try several, returning a stringified number (no commas).
    """
    for k in ("amount_due", "amountDue", "total_due", "totalDue", "due", "amount"):
        v = hit.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return str(v)
        return _norm(str(v).replace(",", "").replace("$", ""))
    return "0"

def _extract_zip(hit: Dict) -> str:
    for k in ("situs_zip", "zip", "zipcode", "mailing_zip"):
        v = _norm(hit.get(k))
        if v:
            # normalize ZIP like 32209 or 32209-1234 -> keep first 5
            v = v.replace(" ", "")
            return v.split("-")[0][:5]
    return ""

def _extract_address(hit: Dict) -> str:
    for k in ("situs_address", "site_address", "address", "property_address", "situs_location"):
        v = _norm(hit.get(k))
        if v:
            return v
    # Some datasets store components
    street = _norm(hit.get("situs_street"))
    city = _norm(hit.get("situs_city"))
    if street and city:
        return f"{street}, {city}"
    return ""

def _extract_owner(hit: Dict) -> str:
    for k in ("owner_name", "owner", "name", "owner1"):
        v = _norm(hit.get(k))
        if v:
            return v
    return ""

def _extract_parcel(hit: Dict) -> str:
    for k in ("account", "account_id", "parcel", "parcel_id", "folio", "alternate_key"):
        v = _norm(hit.get(k))
        if v:
            return v
    return ""

def _hit_to_row(hit: Dict) -> Dict:
    return {
        "owner": _extract_owner(hit),
        "address": _extract_address(hit),
        "parcel": _extract_parcel(hit),
        "zip": _extract_zip(hit),
        "distress": "Tax",  # this endpoint is specifically tax
        "amountDue": _extract_amount(hit),
    }

def _algolia_query(
    query: str,
    hits_per_page: int = 100,
) -> List[Dict]:
    """
    Calls Algolia with the same structure you saw in DevTools.
    We only set the params we actually need; the rest are optional.
    """
    params = (
        "clickAnalytics=true"
        "&facets=[]"
        "&highlightPreTag=__ais-highlight__"
        "&highlightPostTag=__/ais-highlight__"
        f"&hitsPerPage={hits_per_page}"
        f"&query={requests.utils.quote(query)}"
    )

    payload = {
        "requests": [
            {
                "indexName": ALG_INDEX,
                "params": params,
            }
        ]
    }

    r = requests.post(_algolia_endpoint(), headers=_algolia_headers(), data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    body = r.json()
    results = body.get("results") or []
    if not results:
        return []
    hits = results[0].get("hits") or []
    return hits

def _algolia_batch_from_terms(terms: List[str]) -> List[Dict]:
    """
    Given a list of parcel/account/owner or free text terms,
    combine unique normalized rows from Algolia.
    """
    rows: List[Dict] = []
    seen = set()
    for i, term in enumerate(terms, start=1):
        term = _norm(term)
        if not term:
            continue
        try:
            hits = _algolia_query(term, hits_per_page=200)
        except Exception as e:
            app.logger.error("Algolia query failed for term %s: %s", term, e)
            continue

        for h in hits:
            row = _hit_to_row(h)
            key = (row["parcel"], row["amountDue"])
            if not row["parcel"]:
                continue
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows

def _fallback_download_csv(url: str) -> List[Dict]:
    """
    Download a CSV from GitHub raw and map its columns into our schema.
    Your CSV header can be any of:
      owner, owner_name
      address, situs_address, property_address
      parcel, parcel_id, account, account_id
      zip, situs_zip, zipcode
      amountDue, amount_due, total_due, amount
      distress (optional; default 'Tax')
    """
    if not url:
        return []
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    text = r.text
    buf = io.StringIO(text)
    reader = csv.DictReader(buf)
    rows: List[Dict] = []
    for raw in reader:
        row = {
            "owner": _norm(raw.get("owner") or raw.get("owner_name") or raw.get("name")),
            "address": _norm(raw.get("address") or raw.get("situs_address") or raw.get("property_address")),
            "parcel": _norm(raw.get("parcel") or raw.get("parcel_id") or raw.get("account") or raw.get("account_id")),
            "zip": _norm(raw.get("zip") or raw.get("situs_zip") or raw.get("zipcode")),
            "distress": _norm(raw.get("distress") or "Tax"),
            "amountDue": _norm(
                (raw.get("amountDue") or raw.get("amount_due") or raw.get("total_due") or raw.get("amount") or "0")
            ).replace("$", "").replace(",", ""),
        }
        if any(row.values()):
            rows.append(row)
    return rows

# --------------------------
# API routes
# --------------------------
@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "csv_exists": CSV_PATH.exists(),
        "csv_size": CSV_PATH.stat().st_size if CSV_PATH.exists() else 0,
        "algolia_index": ALG_INDEX,
    })

@app.get("/api/debug/file/<path:subpath>")
def api_debug_file(subpath: str):
    """Check a file's existence & size on disk."""
    target = ROOT / subpath
    return jsonify({
        "exists": target.exists(),
        "path": str(target),
        "size": target.stat().st_size if target.exists() else 0
    })

@app.get("/api/algolia-debug")
def api_algolia_debug():
    """Hit Algolia once and show first hit structure to confirm keys."""
    q = request.args.get("q", "").strip() or "030147-0432"
    hits = _algolia_query(q, hits_per_page=15)
    sample = hits[0] if hits else {}
    first_keys = list(sample.keys())[:25]
    return jsonify({
        "query": q,
        "first_hit_keys": first_keys,
        "first_hit_sample": sample,
        "status": "success",
    })

@app.get("/api/refresh")
def api_refresh():
    """
    Refresh data with priority:
      1) If ?q= or ?terms= provided → query Algolia for those terms.
      2) Else try Algolia using a few sensible defaults (noisy but useful):
         - last 4 alg terms common: 'Tax', a sample parcel '030147-0432'
      3) If Algolia returns nothing or errors → fallback GH_RAW_URL (if set).
    """
    terms_param = request.args.get("terms", "").strip()
    q_param = request.args.get("q", "").strip()
    use_fallback_only = request.args.get("fallback", "").lower() in ("1", "true", "yes")

    out_rows: List[Dict] = []

    try:
        if not use_fallback_only:
            if terms_param:
                terms = [t.strip() for t in terms_param.split(",") if t.strip()]
            elif q_param:
                terms = [q_param]
            else:
                # sensible defaults: sample parcel + a generic term
                terms = ["030147-0432", "tax lien", "tax delinquent duval"]
            out_rows = _algolia_batch_from_terms(terms)
    except Exception as e:
        app.logger.error("Algolia batch failed: %s", e)

    # Fallback if empty
    if not out_rows and GH_RAW_URL:
        try:
            out_rows = _fallback_download_csv(GH_RAW_URL)
            source = f"github:{GH_RAW_URL}"
            rows, bytes_written = _write_rows(out_rows)
            return jsonify({
                "status": "success",
                "message": "Fallback scrape filled CSV",
                "rows": rows,
                "bytes": bytes_written,
                "source": source
            })
        except Exception as e:
            app.logger.error("Fallback GitHub CSV failed: %s", e)

    # Persist what we got (even if zero → it still writes headers)
    rows, bytes_written = _write_rows(out_rows)
    return jsonify({
        "status": "success",
        "message": "Leads updated",
        "rows": rows,
        "bytes": bytes_written,
        "source": f"algolia:{ALG_INDEX}"
    })

@app.get("/api/leads")
def api_leads():
    """
    Filter & return rows currently on disk.
    Query params:
      q       = search owner/address/parcel (substring, case-insensitive)
      zip     = exact 5-digit zip
      min     = minimum amountDue (number)
      max     = maximum amountDue (number)
      sources = comma list (only 'tax' matters here)
      limit   = max rows (default 250)
    """
    rows = _read_csv()
    q = (request.args.get("q") or "").strip().lower()
    zip_code = (request.args.get("zip") or "").strip()
    min_amt = request.args.get("min") or request.args.get("min_amount") or ""
    max_amt = request.args.get("max") or request.args.get("max_amount") or ""
    limit = int(request.args.get("limit") or 250)

    def as_num(s):
        try:
            return float(str(s).replace(",", "").replace("$", "").strip())
        except Exception:
            return 0.0

    out = []
    for r in rows:
        if q:
            hay = " ".join([r.get("owner",""), r.get("address",""), r.get("parcel","")]).lower()
            if q not in hay:
                continue
        if zip_code and zip_code != (r.get("zip") or "").strip():
            continue
        if min_amt and as_num(r.get("amountDue", 0)) < as_num(min_amt):
            continue
        if max_amt and as_num(r.get("amountDue", 0)) > as_num(max_amt):
            continue
        out.append(r)
        if len(out) >= limit:
            break

    return jsonify({"status": "success", "count": len(out), "rows": out})

@app.get("/export.csv")
def export_csv():
    """Download the on-disk CSV."""
    _ensure_csv()
    return send_file(
        CSV_PATH,
        mimetype="text/csv",
        as_attachment=True,
        download_name="leads.csv"
    )

# --------------------------
# Static index (optional)
# --------------------------
@app.get("/")
def index():
    # Serve the static index file if it exists, else a tiny placeholder.
    idx = ROOT / "index.html"
    if idx.exists():
        return idx.read_text(encoding="utf-8")
    return Response("<h1>Distress Intelligence API</h1>", mimetype="text/html")

# --------------------------
# Entrypoint
# --------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
