from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8", errors="ignore")

if "/api/delinquent.csv" not in t:
    insert = r'''
@app.get("/api/delinquent.csv")
def api_delinquent_csv():
    import io, csv as _csv
    q = (request.args.get("q") or "").strip().upper()
    zipf = (request.args.get("zip") or "").strip()
    still = (request.args.get("still") or "").strip().lower()
    min_due = request.args.get("min_due")
    try:
        page = int(request.args.get("page") or 1)
    except Exception:
        page = 1
    try:
        page_size = int(request.args.get("page_size") or 500)
    except Exception:
        page_size = 500
    page = max(1, page)
    page_size = max(1, min(2000, page_size))

    rows = _load_delinquent_rows()

    if zipf:
        rows = [r for r in rows if str(r.get("lienhub_situs_zip","")).startswith(zipf)]

    if min_due is not None and str(min_due).strip() != "":
        md = _to_float(min_due)
        rows = [r for r in rows if _to_float(r.get("live_total_due",0)) >= md]

    if still in {"true","false"}:
        want = (still == "true")
        rows = [r for r in rows if str(r.get("still_delinquent","")).strip().lower() == str(want).lower()]

    if q:
        def hay(r):
            return " ".join([
                str(r.get("lienhub_account_no","")),
                str(r.get("lienhub_owner","")),
                str(r.get("lienhub_situs_address","")),
                str(r.get("lienhub_situs_city","")),
                str(r.get("lienhub_situs_zip","")),
            ]).upper()
        rows = [r for r in rows if q in hay(r)]

    start = (page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]

    out = io.StringIO()
    if page_rows:
        cols = list(page_rows[0].keys())
    else:
        cols = ["lienhub_account_no","lienhub_situs_address","lienhub_situs_zip","lienhub_face_amount","live_total_due","still_delinquent"]
    w = _csv.DictWriter(out, fieldnames=cols)
    w.writeheader()
    for r in page_rows:
        w.writerow({k: r.get(k,"") for k in cols})

    from flask import Response
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=delinquent.csv"}
    )
'''
    anchor = '@app.post("/api/rebuild_delinquent")'
    if anchor in t:
        t = t.replace(anchor, insert + "\n" + anchor, 1)
        p.write_text(t, encoding="utf-8")
        print("PATCHED: /api/delinquent.csv")
    else:
        print("ANCHOR NOT FOUND: did not patch")
else:
    print("SKIP: /api/delinquent.csv already exists")
