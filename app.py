import os, re, csv, json, pathlib, logging
from flask import Flask, request, jsonify, send_from_directory
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

APP_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR  = (APP_ROOT / "data"); DATA_DIR.mkdir(exist_ok=True)
LEADS_CSV = DATA_DIR / "leads.csv"

GH_RAW_URL = (os.getenv("GH_RAW_URL","").strip())

# ---- Algolia (public search key) ----
ALG_APP_ID = "0LWZO52LS2"
ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
ALG_INDEX  = "fl-duval.property_tax"
ALG_ENDPOINT = f"https://{ALG_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

HEADERS = ["address","zip","parcel","distress","amountDue","owner"]

app = Flask(__name__, static_folder=str(APP_ROOT), static_url_path="")

# ============== helpers ==============
def ensure_headers():
    if not LEADS_CSV.exists() or LEADS_CSV.stat().st_size == 0:
        with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADERS)

def csv_rows_count():
    if not LEADS_CSV.exists() or LEADS_CSV.stat().st_size == 0:
        return 0
    with LEADS_CSV.open("r", encoding="utf-8", newline="") as f:
        return max(0, sum(1 for _ in f) - 1)

def clean_amount(x):
    if x is None: return 0.0
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(x)) or 0)
    except Exception:
        return 0.0

def first_nonempty(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "", [], {}):
            return v
    return ""

def join_addr(*parts):
    parts = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(dict.fromkeys(parts))

ZIP_RE    = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
PARCEL_RE = re.compile(r"\b\d{6,}\b")
ADDR_RE   = re.compile(r"\b\d+\s+[A-Za-z0-9][^\n,]{2,}")

def pick_by_keys(d, *candidates):
    for k in candidates:
        if k in d and str(d[k]).strip():
            return d[k]
    return None

def any_key_contains(d, substrs):
    for k,v in d.items():
        if any(s in k.lower() for s in substrs) and str(v).strip():
            return v
    return None

def find_zip(d):
    # exact keys first
    z = pick_by_keys(d, "zip","zipcode","zip_code","postal_code","situs_zip","mailing_zip")
    if not z:
        # try to extract from any string field
        for v in d.values():
            if isinstance(v, str):
                m = ZIP_RE.search(v)
                if m: return m.group(1)
    return str(z or "")

def find_parcel(d):
    p = pick_by_keys(d, "parcel","parcel_id","account","account_number","folio")
    if p: return str(p)
    # look through any keys mentioning parcel/folio/account
    v = any_key_contains(d, ["parcel","folio","account"])
    if v: return str(v)
    # fallback: longest 6+ digit group in any field
    best = ""
    for v in d.values():
        if isinstance(v, (str,int)):
            for m in PARCEL_RE.findall(str(v)):
                if len(m) > len(best): best = m
    return best

def find_owner(d):
    v = pick_by_keys(d, "owner","owner_name","owner1","owner2","name","mailing_name","mail_name","owner_name1")
    if v: return str(v)
    # any field including 'owner' or 'name'
    v = any_key_contains(d, ["owner","name"])
    return str(v or "")

def find_amount(d):
    v = pick_by_keys(
        d, "amount_due","total_due","balance_due","balance","tax_due",
        "amount","unpaid_balance","current_due","delinquent_amount","totalDue","amountDue"
    )
    if v is None:
        # try any key that looks like an amount/due/balance
        for k,val in d.items():
            lk = k.lower()
            if any(s in lk for s in ["due","balance","amount"]):
                amt = clean_amount(val)
                if amt > 0:
                    v = amt
                    break
    return f"{clean_amount(v):.2f}"

def find_address(d):
    v = pick_by_keys(
        d, "property_address","situs_address","situs_addr1","address","address_line",
        "situs_addr2","situs_city","situs_state","mailing_address","mail_addr1","mail_addr2",
    )
    if isinstance(v, str) and v.strip():
        return v.strip()
    # combine bits that look like address parts
    combo = join_addr(
        d.get("situs_addr1"), d.get("situs_addr2"),
        d.get("situs_city"), d.get("situs_state"),
        d.get("address_line"), d.get("mail_addr1"), d.get("mail_addr2"),
        d.get("street"), d.get("street_name")
    )
    if combo:
        return combo
    # last resort: first field that looks like "123 Something"
    for val in d.values():
        if isinstance(val, str) and ADDR_RE.search(val):
            return val.strip()
    return ""

def normalize_hit_smart(hit: dict):
    addr   = find_address(hit)
    zipc   = find_zip(hit)
    parcel = find_parcel(hit)
    owner  = find_owner(hit)
    amount = find_amount(hit)
    return [addr, str(zipc), str(parcel), "Tax", amount, owner]

def write_rows(rows):
    with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)

# ============== sources ==============
def refresh_from_github():
    if not GH_RAW_URL or GH_RAW_URL.endswith("%0A"):
        return {"status":"skipped","source":"github","message":"GH_RAW_URL not set/has newline"}
    try:
        r = requests.get(GH_RAW_URL, timeout=30)
        if r.status_code == 404:
            return {"status":"error","source":"github","message":"GitHub 404"}
        r.raise_for_status()
        txt = r.text
        if not txt.strip():
            return {"status":"error","source":"github","message":"GitHub CSV empty"}
        with LEADS_CSV.open("w", encoding="utf-8", newline="") as f:
            f.write(txt if txt.endswith("\n") else txt + "\n")
        return {"status":"success","source":"github","bytes":LEADS_CSV.stat().st_size}
    except Exception as e:
        return {"status":"error","source":"github","message":str(e)}

def refresh_from_algolia(limit=1000):
    try:
        headers = {
            "x-algolia-application-id": ALG_APP_ID,
            "x-algolia-api-key": ALG_API_KEY,
            "Content-Type": "application/json",
            "x-algolia-agent": "python(custom)"
        }
        params = "query=&hitsPerPage={}".format(min(1000, int(limit)))
        payload = {"requests":[{"indexName": ALG_INDEX, "params": params}]}
        r = requests.post(ALG_ENDPOINT, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        hits = (data.get("results") or [{}])[0].get("hits") or []
        if not hits:
            return {"status":"error","source":"algolia","message":"0 hits"}

        rows = []
        for h in hits:
            row = normalize_hit_smart(h)
            # require at least an address or parcel to keep
            if any(str(c).strip() for c in (row[0], row[2])):
                rows.append(row)

        if not rows:
            return {"status":"error","source":"algolia","message":"normalized rows empty"}

        write_rows(rows)
        return {"status":"success","source":"algolia","rows":len(rows),"bytes":LEADS_CSV.stat().st_size}
    except Exception as e:
        return {"status":"error","source":"algolia","message":str(e)}

# ============== routes ==============
@app.route("/")
def serve_index():
    path = APP_ROOT / "index.html"
    if not path.exists():
        return ("Not Found", 404)
    return send_from_directory(str(APP_ROOT), "index.html")

@app.route("/data/<path:filename>")
def serve_data(filename):
    return send_from_directory(str(DATA_DIR), filename)

@app.route("/api/health")
def api_health():
    return jsonify({
        "status":"ok",
        "csv_exists": LEADS_CSV.exists(),
        "csv_bytes": LEADS_CSV.stat().st_size if LEADS_CSV.exists() else 0,
        "rows": csv_rows_count(),
        "gh_raw_url_set": bool(GH_RAW_URL)
    })

@app.route("/api/algolia-debug")
def algolia_debug():
    # returns first 3 hits keys so we can see what's actually there
    try:
        headers = {
            "x-algolia-application-id": ALG_APP_ID,
            "x-algolia-api-key": ALG_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {"requests":[{"indexName": ALG_INDEX, "params": "query=&hitsPerPage=3"}]}
        r = requests.post(ALG_ENDPOINT, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        hits = (data.get("results") or [{}])[0].get("hits") or []
        summary = []
        for h in hits:
            summary.append(sorted(list(h.keys())))
        return jsonify({"status":"success","keys_per_hit": summary})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.route("/api/refresh")
def api_refresh():
    ensure_headers()
    source = (request.args.get("source") or "both").lower()
    results = {}

    if source in ("github","both"):
        results["github"] = refresh_from_github()

    need_algolia = (
        source in ("algolia","both")
        or csv_rows_count() < 5
        or (results.get("github",{}).get("status") != "success")
    )
    if need_algolia:
        results["algolia"] = refresh_from_algolia(1000)

    results["final"] = {
        "rows": csv_rows_count(),
        "bytes": LEADS_CSV.stat().st_size if LEADS_CSV.exists() else 0
    }
    return jsonify(results)

@app.route("/api/leads")
def api_leads():
    ensure_headers()
    zip_filter = (request.args.get("zip") or "").strip()
    min_amount = clean_amount(request.args.get("min_amount"))
    max_amount = clean_amount(request.args.get("max_amount")) if request.args.get("max_amount") else None
    sources = [s.strip().lower() for s in (request.args.get("sources") or "tax").split(",") if s.strip()]

    out = []
    with LEADS_CSV.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            distress = (row.get("distress") or row.get("source") or "").lower()
            if sources and ("tax" not in sources) and (distress not in sources):
                continue
            if zip_filter and (str(row.get("zip","")).strip() != zip_filter):
                continue
            due = clean_amount(row.get("amountDue") or row.get("amount_due"))
            if min_amount and due < min_amount:
                continue
            if max_amount and max_amount > 0 and due > max_amount:
                continue
            out.append({
                "address": row.get("address",""),
                "zip": row.get("zip",""),
                "parcel": row.get("parcel",""),
                "distress": row.get("distress",""),
                "amountDue": f"{due:.2f}",
                "owner": row.get("owner",""),
            })
    return jsonify({"status":"success","count":len(out),"rows":out[:1000]})

@app.route("/debug/file/<path:rel>")
def debug_file(rel):
    p = (APP_ROOT / rel)
    return jsonify({"exists": p.exists(), "path": str(p), "size": p.stat().st_size if p.exists() else 0})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
