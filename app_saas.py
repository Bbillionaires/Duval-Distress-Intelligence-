import os, csv, json, secrets, time, subprocess, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory, redirect, make_response

import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = Path(__file__).resolve().parent
DELINQ_CSV = BASE_DIR / "duval_delinquent_leads_big.csv"
REBUILD_SCRIPT = BASE_DIR / "duval_build_big_delinquent.py"

APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
COOKIE_NAME = os.getenv("SESSION_COOKIE", "di_session")

# Render provides DATABASE_URL for Postgres. Fallback to local sqlite-like behavior is not used here.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@local")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")  # change in Render env

def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it locally or in Render env vars.")
    # Render Postgres URLs usually work with sslmode=require
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn = dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    return psycopg2.connect(dsn)

def db_init():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              email TEXT UNIQUE NOT NULL,
              pw_hash TEXT NOT NULL,
              is_admin BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TIMESTAMPTZ NOT NULL
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS saved_searches (
              id SERIAL PRIMARY KEY,
              user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              params JSONB NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            # seed admin user if missing
            cur.execute("SELECT id FROM users WHERE email=%s", (DEFAULT_ADMIN_EMAIL,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users(email,pw_hash,is_admin) VALUES(%s,%s,TRUE)",
                    (DEFAULT_ADMIN_EMAIL, generate_password_hash(DEFAULT_ADMIN_PASSWORD))
                )

app = Flask(__name__)
app.config["SECRET_KEY"] = APP_SECRET


def read_csv_rows():
    if not DELINQ_CSV.exists():
        return []
    rows = []
    with DELINQ_CSV.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            rows.append(r)
    return rows

def to_float(x):
    try:
        return float(str(x).replace(",", "").strip() or "0")
    except:
        return 0.0

def session_user():
    tok = request.cookies.get(COOKIE_NAME, "")
    if not tok:
        return None
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
              SELECT u.id,u.email,u.is_admin,s.expires_at
              FROM sessions s
              JOIN users u ON u.id=s.user_id
              WHERE s.token=%s
            """, (tok,))
            row = cur.fetchone()
            if not row:
                return None
            if row["expires_at"] < datetime.now(timezone.utc):
                cur.execute("DELETE FROM sessions WHERE token=%s", (tok,))
                return None
            return row

def require_login(admin=False):
    u = session_user()
    if not u:
        return None
    if admin and not u["is_admin"]:
        return None
    return u

@app.get("/")
def index_page():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/admin.html")
@app.get("/admin")
@app.get("/admin/")
def admin_page():
    u = require_login(admin=True)
    if not u:
        return redirect("/login")
    return send_from_directory(BASE_DIR, "admin.html")

@app.get("/login")
@app.get("/login/")
@app.get("/login.html")
def login_page():
    return send_from_directory(BASE_DIR, "login.html")

@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or DEFAULT_ADMIN_EMAIL).strip().lower()
    pw = (data.get("password") or "").strip()
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,email,pw_hash,is_admin FROM users WHERE email=%s", (email,))
            u = cur.fetchone()
            if not u or not check_password_hash(u["pw_hash"], pw):
                return jsonify({"ok": False, "error": "Invalid credentials"}), 401
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(days=7)
            cur.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(%s,%s,%s)", (token, u["id"], expires))
    resp = make_response(jsonify({"ok": True, "email": u["email"], "is_admin": u["is_admin"]}))
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Lax")
    return resp

@app.post("/api/logout")
def api_logout():
    tok = request.cookies.get(COOKIE_NAME, "")
    if tok:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token=%s", (tok,))
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(COOKIE_NAME, "", expires=0)
    return resp

@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "csv_exists": DELINQ_CSV.exists(),
        "csv_path": str(DELINQ_CSV),
        "csv_size": DELINQ_CSV.stat().st_size if DELINQ_CSV.exists() else 0,
        "db_url_set": bool(DATABASE_URL),
        "server_time": datetime.now(timezone.utc).isoformat() + "Z"
    })

@app.get("/api/delinquent")
def api_delinquent():
    # optional filters:
    # still_delinquent=true/false
    # min_due=number
    # q=search in parcel/address
    # page,page_size
    try:
        page = max(1, int(request.args.get("page") or 1))
    except:
        page = 1
    try:
        page_size = max(1, min(500, int(request.args.get("page_size") or 50)))
    except:
        page_size = 50

    still = (request.args.get("still_delinquent") or "").strip().lower()
    min_due = request.args.get("min_due")
    q = (request.args.get("q") or "").strip().upper()

    rows = read_csv_rows()

    if still in ("true","false","1","0","yes","no"):
        want = still in ("true","1","yes")
        def b(v):
            return str(v).strip().lower() in ("true","1","yes")
        rows = [r for r in rows if b(r.get("still_delinquent","")) == want]

    if min_due is not None and str(min_due).strip() != "":
        md = to_float(min_due)
        rows = [r for r in rows if to_float(r.get("live_total_due",0)) >= md]

    if q:
        def hay(r):
            return " ".join([
                str(r.get("lienhub_account_no","")),
                str(r.get("lienhub_situs_address","")),
                str(r.get("lienhub_situs_zip","")),
            ]).upper()
        rows = [r for r in rows if q in hay(r)]

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]

    return jsonify({"page": page, "page_size": page_size, "returned": len(page_rows), "total": total, "rows": page_rows})

@app.post("/api/rebuild_delinquent")
def api_rebuild_delinquent():
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    if not REBUILD_SCRIPT.exists():
        return jsonify({"ok": False, "error": "duval_build_big_delinquent.py not found"}), 500
    p = subprocess.run([sys.executable, str(REBUILD_SCRIPT)], cwd=str(BASE_DIR), capture_output=True, text=True)
    return jsonify({
        "ok": (p.returncode == 0),
        "returncode": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
        "csv_path": str(DELINQ_CSV),
        "csv_exists": DELINQ_CSV.exists(),
    }), (200 if p.returncode == 0 else 500)

@app.get("/api/saved_searches")
def api_saved_searches_list():
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,name,params,created_at FROM saved_searches WHERE user_id=%s ORDER BY id DESC", (u["id"],))
            rows = cur.fetchall()
    return jsonify({"ok": True, "rows": rows})

@app.post("/api/saved_searches")
def api_saved_searches_create():
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Saved Search").strip()
    params = data.get("params") or {}
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO saved_searches(user_id,name,params) VALUES(%s,%s,%s)", (u["id"], name, json.dumps(params)))
    return jsonify({"ok": True})

if __name__ == "__main__":
    db_init()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
