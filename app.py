import os
from flask import Flask, request, jsonify
from algoliasearch.search_client import SearchClient

app = Flask(__name__)

# --- Config from environment (set these in Render) ---
ALG_APP_ID = os.environ.get("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.environ.get("ALG_API_KEY")  # put your key in Render env
ALG_INDEX_NAME = os.environ.get("ALG_INDEX", "fl-duval.property_tax")

if not ALG_API_KEY:
    raise RuntimeError("ALG_API_KEY env var is required")

client = SearchClient.create(ALG_APP_ID, ALG_API_KEY)
index = client.init_index(ALG_INDEX_NAME)


def normalize_hit(hit):
    """
    Convert a raw Algolia hit into the normalized row shape
    the frontend expects.
    """
    # Owner
    owner = (
        hit.get("owner_name")
        or hit.get("owner")
        or hit.get("display_name")
        or ""
    )

    # Address – county uses nested address structures; be defensive
    address = ""
    addr = hit.get("address") or hit.get("situs_address") or {}
    if isinstance(addr, dict):
        # Try common fields
        address = (
            addr.get("full")
            or addr.get("line1")
            or addr.get("address")
            or ""
        )
    elif isinstance(addr, str):
        address = addr

    # Parcel / account
    parcel = (
        hit.get("account")
        or hit.get("parcel")
        or hit.get("alternate_keys", {}).get("external_id")
        or ""
    )

    # Zip
    zip_code = (
        hit.get("zip")
        or hit.get("postal_code")
        or hit.get("zipcode")
        or ""
    )

    # Amount due as float
    amount_raw = (
        hit.get("amount_due")
        or hit.get("amountDue")
        or hit.get("amount_due_total")
        or 0
    )
    try:
        amount_due = float(amount_raw)
    except Exception:
        amount_due = 0.0

    # Distress type (we’re only using Tax for now)
    distress = "Tax"

    # Public link if available
    link = ""
    public_url = hit.get("public_url") or hit.get("public_url_")
    if isinstance(public_url, str) and public_url:
        link = public_url
    else:
        # Some Duval hits have a relative URL in external_type/objectID, etc.
        maybe_url = hit.get("url") or hit.get("external_type") or ""
        if isinstance(maybe_url, str) and "county-taxes.net" in maybe_url:
            link = maybe_url

    return {
        "owner": owner,
        "address": address,
        "parcel": parcel,
        "zip": zip_code,
        "distress": distress,
        "amountDue": amount_due,
        "link": link,
    }


@app.route("/api/search")
def search_distress():
    """
    Main search endpoint used by the SPA.

    Modes:
    - If ?account=030147-0432 is present:
        -> look up that single parcel.
    - Else:
        -> use zip + amount range to get many hits.
    """
    account = (request.args.get("account") or "").strip()
    zip_code = (request.args.get("zip") or "").strip()
    min_amount = request.args.get("minAmountDue")
    max_amount = request.args.get("maxAmountDue")

    # ------- MODE 1: single account lookup -------
    if account:
        # query on the account number; limit to a few hits
        res = index.search(
            account,
            {
                "hitsPerPage": 5,
            },
        )
        hits = res.get("hits", [])
        if not hits:
            return jsonify({"status": "success", "count": 0, "rows": []})

        rows = [normalize_hit(hits[0])]
        return jsonify({"status": "success", "count": 1, "rows": rows})

    # ------- MODE 2: broad search by zip + amount range -------
    filters = []
    numeric_filters = []

    if zip_code:
        filters.append(f"zip:{zip_code}")

    try:
        if min_amount not in (None, "", "NaN"):
            min_val = float(min_amount)
            numeric_filters.append(f"amount_due>={min_val}")
    except ValueError:
        pass

    try:
        if max_amount not in (None, "", "NaN"):
            max_val = float(max_amount)
            numeric_filters.append(f"amount_due<={max_val}")
    except ValueError:
        pass

    # Build Algolia params
    params = {
        "hitsPerPage": 1000,  # pull a good chunk; Algolia limits still apply
    }

    if filters:
        params["filters"] = " AND ".join(filters)
    if numeric_filters:
        params["numericFilters"] = numeric_filters

    # Empty query string -> "filter only" search
    res = index.search("", params)
    hits = res.get("hits", [])

    rows = [normalize_hit(h) for h in hits]
    return jsonify({"status": "success", "count": len(rows), "rows": rows})


@app.route("/api/health")
def health():
    """
    Simple health/config check.
    DOES NOT expose your secret key.
    """
    # Check if CSV exists if you still use it; here we just stub that out
    csv_path = os.path.join(os.path.dirname(__file__), "data", "leads.csv")
    csv_exists = os.path.exists(csv_path)
    csv_size = os.path.getsize(csv_path) if csv_exists else 0

    return jsonify(
        {
            "status": "success",
            "algolia_app_id": ALG_APP_ID,
            "algolia_index": ALG_INDEX_NAME,
            "csv_exists": csv_exists,
            "csv_size": csv_size,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
