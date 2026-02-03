"""
Flask App with Automated Tax Lead Scraping - SaaS Ready

Features:
1. Automated scraping via cron/scheduler
2. API endpoints to trigger scrapes
3. View scrape jobs and results
4. Admin controls
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, redirect, make_response
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

# Database check on startup
print("\n" + "=" * 70)
print("🔍 DATABASE CHECK ON STARTUP")
print("=" * 70)
try:
    DATABASE_URL = os.getenv("DATABASE_URL")
    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        cur.execute('SELECT COUNT(*) FROM properties')
        total = cur.fetchone()[0]
        print(f"📊 Total properties in database: {total}")
        
        cur.execute('SELECT COUNT(*) FROM properties WHERE has_tax_deed_notice = TRUE')
        ntd = cur.fetchone()[0]
        print(f"🎯 Properties with Tax Deed Notice: {ntd}")
        
        cur.execute('SELECT stage, COUNT(*) FROM properties GROUP BY stage ORDER BY COUNT(*) DESC')
        print("📈 By stage:")
        for stage, count in cur.fetchall():
            print(f"   {stage}: {count}")
        
        conn.close()
    else:
        print("⚠️  DATABASE_URL not set")
except Exception as e:
    print(f"⚠️  Database check failed: {e}")
print("=" * 70 + "\n")

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
COOKIE_NAME = os.getenv("SESSION_COOKIE", "di_session")

DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@local")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")

app = Flask(__name__)
app.config["SECRET_KEY"] = APP_SECRET


def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn = dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    return psycopg2.connect(dsn)


def db_init():
    """Initialize all database tables"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            # Users table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              email TEXT UNIQUE NOT NULL,
              pw_hash TEXT NOT NULL,
              is_admin BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            
            # Sessions table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TIMESTAMPTZ NOT NULL
            );
            """)
            
            # Scrape jobs table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_jobs (
              id SERIAL PRIMARY KEY,
              job_type TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              started_at TIMESTAMPTZ,
              completed_at TIMESTAMPTZ,
              properties_found INT DEFAULT 0,
              properties_updated INT DEFAULT 0,
              error_message TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status ON scrape_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_scrape_jobs_created ON scrape_jobs(created_at DESC);
            """)
            
            # Properties table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS properties (
              id SERIAL PRIMARY KEY,
              parcel TEXT UNIQUE NOT NULL,
              stage TEXT NOT NULL,
              certificate_number TEXT,
              certificate_year INT,
              certificate_age_months INT,
              owner TEXT,
              address TEXT,
              city TEXT,
              zip TEXT,
              face_amount NUMERIC(12,2),
              current_total_due NUMERIC(12,2),
              current_delinquent NUMERIC(12,2),
              has_tax_deed_notice BOOLEAN DEFAULT FALSE,
              public_url TEXT,
              last_verified_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_properties_parcel ON properties(parcel);
            CREATE INDEX IF NOT EXISTS idx_properties_stage ON properties(stage);
            CREATE INDEX IF NOT EXISTS idx_properties_verified ON properties(last_verified_at DESC);
            """)
            
            # Property history
            cur.execute("""
            CREATE TABLE IF NOT EXISTS property_history (
              id SERIAL PRIMARY KEY,
              parcel TEXT NOT NULL,
              total_due NUMERIC(12,2),
              delinquent_amount NUMERIC(12,2),
              stage TEXT,
              snapshot_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_history_parcel ON property_history(parcel);
            CREATE INDEX IF NOT EXISTS idx_history_date ON property_history(snapshot_date DESC);
            """)
            
            # Tax deed notices
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tax_deed_notices (
              id SERIAL PRIMARY KEY,
              parcel TEXT NOT NULL,
              doc_number TEXT,
              recorded_date TEXT,
              party_names TEXT,
              legal_description TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ntd_parcel ON tax_deed_notices(parcel);
            """)
            
            # Seed admin user
            cur.execute("SELECT id FROM users WHERE email=%s", (DEFAULT_ADMIN_EMAIL,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users(email,pw_hash,is_admin) VALUES(%s,%s,TRUE)",
                    (DEFAULT_ADMIN_EMAIL, generate_password_hash(DEFAULT_ADMIN_PASSWORD))
                )


def session_user():
    """Get current user from session"""
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
    """Require user to be logged in"""
    u = session_user()
    if not u:
        return None
    if admin and not u["is_admin"]:
        return None
    return u


# ========== ROUTES ==========

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


# ========== AUTH API ==========

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
            
            from datetime import timedelta
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(days=7)
            cur.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(%s,%s,%s)", 
                       (token, u["id"], expires))
    
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


# ========== SCRAPING API ==========

@app.post("/api/trigger_scrape")
def api_trigger_scrape():
    """Manually trigger a scrape job (admin only)"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Run scraper in background
    scraper_script = BASE_DIR / "automated_scraper.py"
    
    if not scraper_script.exists():
        return jsonify({"ok": False, "error": "Scraper script not found"}), 500
    
    try:
        # Start scraper as background process
        subprocess.Popen(
            [sys.executable, str(scraper_script)],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        return jsonify({
            "ok": True,
            "message": "Scrape job started in background"
        })
    
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/scrape_jobs")
def api_scrape_jobs():
    """Get list of scrape jobs"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        limit = int(request.args.get("limit", "20"))
    except:
        limit = 20
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
            SELECT * FROM scrape_jobs
            ORDER BY created_at DESC
            LIMIT %s
            """, (limit,))
            jobs = cur.fetchall()
    
    return jsonify({"ok": True, "jobs": jobs})


@app.get("/api/properties")
def api_properties():
    """Get properties from database"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Filters
    stage = request.args.get("stage")
    min_due = request.args.get("min_due")
    zip_code = request.args.get("zip")
    
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = max(1, min(500, int(request.args.get("page_size", "50"))))
    except:
        page = 1
        page_size = 50
    
    # Build query
    where_clauses = []
    params = []
    
    if stage:
        where_clauses.append("stage = %s")
        params.append(stage)
    
    if min_due:
        try:
            where_clauses.append("current_total_due >= %s")
            params.append(float(min_due))
        except:
            pass
    
    if zip_code:
        where_clauses.append("zip LIKE %s")
        params.append(f"{zip_code}%")
    
    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Count total
            cur.execute(f"SELECT COUNT(*) as total FROM properties {where_sql}", params)
            total = cur.fetchone()["total"]
            
            # Get page
            offset = (page - 1) * page_size
            cur.execute(f"""
            SELECT * FROM properties
            {where_sql}
            ORDER BY last_verified_at DESC NULLS LAST
            LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            
            rows = cur.fetchall()
    
    return jsonify({
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "returned": len(rows),
        "rows": rows
    })


@app.get("/api/stats")
def api_stats():
    """Get overall statistics"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Overall counts
            cur.execute("""
            SELECT 
                COUNT(*) as total_properties,
                COUNT(*) FILTER (WHERE stage = 'sweet_spot') as sweet_spot_count,
                COUNT(*) FILTER (WHERE has_tax_deed_notice = TRUE) as with_ntd,
                SUM(current_total_due) as total_amount_due
            FROM properties
            """)
            overall = cur.fetchone()
            
            # By stage
            cur.execute("""
            SELECT stage, COUNT(*) as count
            FROM properties
            GROUP BY stage
            ORDER BY count DESC
            """)
            by_stage = cur.fetchall()
            
            # Recent jobs
            cur.execute("""
            SELECT status, COUNT(*) as count
            FROM scrape_jobs
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY status
            """)
            recent_jobs = cur.fetchall()
    
    return jsonify({
        "ok": True,
        "overall": overall,
        "by_stage": by_stage,
        "recent_jobs": recent_jobs
    })


@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "db_url_set": bool(DATABASE_URL),
        "server_time": datetime.now(timezone.utc).isoformat() + "Z"
    })


# Initialize database on startup (always run, not just when called directly)
db_init()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)