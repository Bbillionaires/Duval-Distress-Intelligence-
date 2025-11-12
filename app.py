# app.py
import json
import os
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List

import requests
from flask import Flask, jsonify, request, send_from_directory

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

# Use env vars if present, otherwise your captured values
ALG_APP_ID = os.getenv("ALG_APP_ID", "0LWZO52LS2")
ALG_API_KEY = os.getenv("ALG_API_KEY", "c0745578b56854a1b90ed57b63fbf0ba")
ALG_INDEX = os.getenv("ALG_INDEX", "fl-duval.property_tax")

ALG_ENDPOINT = f"https://{ALG_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

app = Flask(__name__, static_folder=None)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def algolia_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Algolia-Application-Id": ALG_APP_ID,
        "X-Algolia-API-Key": ALG_API_KEY,
    }


def clean_amount(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def pick(hit: Dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = hit.get(k)
        if v not in (None, "", []):
            return str(v).strip()
    return default


def map_hit_to_lead(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one Algolia hit into our normalized lead object."""
    owner = pick(hit, "owner", "owner_name", "owner1", "name", default="")
    address = pick(
        hit,
        "situs_address",
        "property_address",
        "address",
        "site_address",
        "Location",
        default="",
    )
    parcel = pick(hit, "account", "account_id", "parcel", "parcel_id", "folio", default="")
    zip_code = pick(hit, "situs_zip", "zip", "zipcode", "zip_code", default="")
    amount_raw = pick(
        hit,
        "amount_due",
        "amountDue",
        "total_due",
        "totalDue",
        "delinquent_amount",
        "amount",
        "balance",
        default="0",
    )
    amount = clean_amount(amount_raw)
    distress = pick(hit, "distress", "source", "status", default="Tax")

    return {
        "owner": owner,
        "address": address,
        "parcel": parcel,
        "zip": zip_code,
        "distress": distress or "Tax",
        "amountDue": f"{amount:.2f}",
    }


def algolia_search(query: str, hits_per_page: int = 100) -> List[Dict[str, Any]]:
    """
    Call Algolia using the SAME shape as the browser payload:

      {
        "requests": [{
          "indexName": "fl-duval.property_tax",
          "params": "clickAnalytics=true&facets=[]&highlightPreTag=__ais-highlight__&highlightPostTag=__/ais-highlight__&hitsPerPage=15&query=030147-0432&tagFilters="
        }]
      }
    """

    # Build a dict, then let urllib.urlencode handle encoding
    query_params = {
        "clickAnalytics": "true",
        "facets": "[]",
        "highlightPreTag": "__ais-highlight__",
        "highlightPostTag": "__/ais-highlight__",
        "hitsPerPage": str(hits_per_page),
        "query": query,        # NOT pre-encoded; urlencode will do it
        "tagFilters": "",
    }
    params_str = urllib.parse.urlencode(query_params)

    payload = {
        "requests": [
            {
                "indexName": ALG_INDEX,
                "params": params_str,
            }
        ]
    }

    resp = requests.post(
        ALG_ENDPOINT,
        headers=algolia_headers(),
        data=json.dumps(payload),
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    if not results:
        return []
    hits = results[0].get("hits") or []
    return hits


# -------------------------------------------------------------------
# API routes
# -------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "status": "ok",
            "algolia": {
                "app_id": ALG_APP_ID,
                "index": ALG_INDEX,
                "endpoint": ALG_ENDPOINT,
            },
        }
    )


@app.get("/api/algolia-debug")
def api_algolia_debug():
    """
    Check what Algolia returns for a test query.
    Example:
      /api/algolia-debug?q=030147-0432
    """
    q = request.args.get("q", "").strip() or "030147-0432"
    try:
        hits = algolia_search(q, hits_per_page=5)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    sample = hits[0] if hits else {}
    return jsonify(
        {
            "status": "success",
            "query": q,
            "hit_count": len(hits),
            "first_hit_keys": list(sample.keys()) if sample else [],
            "first_hit_sample": sample,
        }
    )


@app.get("/api/leads")
def api_leads():
    """
    Main endpoint the UI uses.

    Query params:
      q        = search text (parcel, owner, or address)
      zip      = optional zip filter
      min      = optional min amount
      max      = optional max amount
      limit    = max results (default 100)
    """
    q = (request.args.get("q") or "").strip()
    zip_filter = (request.args.get("zip") or "").strip()
    min_raw = request.args.get("min") or request.args.get("min_amount") or ""
    max_raw = request.args.get("max") or request.args.get("max_amount") or ""
    limit_raw = request.args.get("limit") or "100"

    search_term = q or zip_filter or "Duval"

    try:
        limit = max(1, int(limit_raw))
    except Exception:
        limit = 100

    # Call Algolia
    try:
        hits = algolia_search(search_term, hits_per_page=limit)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    # Map + filter
    leads = [map_hit_to_lead(h) for h in hits]

    try:
        min_amount = float(min_raw) if min_raw not in ("", None) else None
    except Exception:
        min_amount = None
    try:
        max_amount = float(max_raw) if max_raw not in ("", None) else None
    except Exception:
        max_amount = None

    out: List[Dict[str, Any]] = []
    for lead in leads:
        amt = clean_amount(lead.get("amountDue"))
        if zip_filter and lead.get("zip") != zip_filter:
            continue
        if min_amount is not None and amt < min_amount:
            continue
        if max_amount is not None and amt > max_amount:
            continue
        if q:
            hay = " ".join(
                [lead.get("owner", ""), lead.get("address", ""), lead.get("parcel", "")]
            ).lower()
            if q.lower() not in hay:
                continue
        out.append(lead)

    return jsonify({"status": "success", "count": len(out), "rows": out})


# -------------------------------------------------------------------
# Static index
# -------------------------------------------------------------------
@app.get("/")
def index():
    idx = BASE_DIR / "index.html"
    if idx.exists():
        return idx.read_text(encoding="utf-8")
    return "<h1>Distress Intelligence API</h1><p>index.html missing.</p>"


@app.get("/<path:filename>")
def static_files(filename: str):
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
