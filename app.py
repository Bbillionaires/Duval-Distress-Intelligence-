import os, csv, datetime, logging
from flask import Flask, jsonify, send_from_directory, request, abort
from flask_cors import CORS
import requests

# ── Logging Setup ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("distress")

# ── Paths & App ─────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, static_folder=ROOT, static_url_path="")
CORS(app)

DATA_FILE = os.path.join(ROOT, "data", "leads.csv")
GH_RAW_URL = os.environ.get(
    "GH_RAW_URL",
    "https://raw.githubusercontent.com/Bbillionaires/Duval-Distress-Intelligence-/Azoth-made-1st/data/leads.csv",
)

# ── Debug helpers ───────────────────────────────────────────────
def list_root():
    try:
        return sorted(os.listdir(ROOT))
    except Exception as e:
        return [f"<< listdir failed: {e} >>"]

@app.route("/debug/ls")
def debug_ls():
    return jsonify({"cwd": os.getcwd(), "root": ROOT, "files": list_root()})

@app.route("/debug/file/<path:fname>")
def debug_file(fname):
    p = os.path.join(ROOT, fname)
    return jsonify({
        "exists": os.path.exists(p),
        "size": os.path.getsize(p) if os.path.exists(p) else None,
        "path": p
    })

# ── Static & SPA routes ─────────────────────────────────────────
@app.route("/")
def root():
    idx = os.path.join(ROOT, "index.html")
    log.info("Serving index. cwd=%s root=%s exists=%s", os.getcwd(), ROOT, os.path.exists(idx))
    if not os.path.exists(idx):
        log.error("index.html not found. Files: %s", list_root())
        abort(404)
    return send_from_directory(ROOT, "index.html")

@app.route("/<path:path>")
def any_path(path):
    real = os.path.join(ROOT, path)
    if os.path.isfile(real):
        return send_from_directory(ROOT, path)
    # SPA fallback
    idx = os.path.join(ROOT, "index.html")
    if not os.path.exists(idx):
        abort(404)
    return send_from_directory(ROOT, "index.html")

# ── Healthcheck ─────────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"ok": True})

# ── Load leads ──────────────────────────────────────────────────
def load_leads():
    rows = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    return rows

# ── Leads endpoint ──────────────────────────────────────────────
@app.route("/api/leads", methods=["GET"])
def api_leads():
    return jsonify(load_leads())

# ── Step 1: Refresh (GET + POST) ────────────────────────────────
@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    """
    Pull latest leads.csv from GitHub Raw and save to /data
    """
    try:
        r = requests.get(GH_RAW_URL, timeout=30)
        r.raise_for_status()
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "wb") as f:
            f.write(r.content)
        size = os.path.getsize(DATA_FILE)
        return jsonify({
            "status": "success",
            "message": "Leads updated from GitHub.",
            "bytes": size
        })
    except Exception as e:
        log.exception("refresh failed")
        return jsonify({"status": "error", "message": str(e)}), 500

# ── Skiptrace proxy (optional) ──────────────────────────────────
@app.route("/api/skiptrace_order", methods=["POST"])
def api_skiptrace():
    url = os.environ.get("GAS_URL", "")
    if not url:
        return jsonify({"status":"error","message":"App Script URL not set"}), 400
    try:
        payload = request.json or {}
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        return jsonify({"status":"ok","message":"Skiptrace queued"})
    except Exception as e:
        log.exception("skiptrace failed")
        return jsonify({"status":"error","message":str(e)}), 500

# ── Admin summary ───────────────────────────────────────────────
@app.route("/api/admin/summary")
def admin_summary():
    leads = load_leads()
    last = None
    if os.path.exists(DATA_FILE):
        last = datetime.datetime.fromtimestamp(os.path.getmtime(DATA_FILE)).isoformat()
    return jsonify({"total_leads": len(leads), "last_updated": last})

# ── Entrypoint ──────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
