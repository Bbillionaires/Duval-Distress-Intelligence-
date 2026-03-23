"""
# VA Portal Complete - 2026-03-18
Flask App with Automated Tax Lead Scraping - SaaS Ready

Features:
1. Automated scraping via cron/scheduler
2. API endpoints to trigger scrapes
3. View scrape jobs and results
4. Admin controls
5. Multi-county support (future-proof)

VERSION: 2026-03-17 - VA Portal Complete with all endpoints
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import io
import tempfile

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, redirect, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import secrets
import stripe

# Import openpyxl for Excel handling
try:
    from openpyxl import load_workbook
    EXCEL_SUPPORT = True
except ImportError:
    EXCEL_SUPPORT = False

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
COOKIE_NAME = os.getenv("SESSION_COOKIE", "di_session")

DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@local")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")

app = Flask(__name__)
app.config["SECRET_KEY"] = APP_SECRET
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max file size
app.config["UPLOAD_FOLDER"] = BASE_DIR / "uploads"

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Allowed file extensions for upload
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# County configuration (future-proof for expansion)
AVAILABLE_COUNTIES = [
    {
        "code": "duval",
        "name": "Duval",
        "state": "FL",
        "enabled": True,
        "price": 99
    },
    {
        "code": "miami-dade",
        "name": "Miami-Dade",
        "state": "FL",
        "enabled": False,
        "price": 99
    },
    {
        "code": "broward",
        "name": "Broward",
        "state": "FL",
        "enabled": False,
        "price": 99
    },
    {
        "code": "palm-beach",
        "name": "Palm Beach",
        "state": "FL",
        "enabled": False,
        "price": 99
    }
]


def db_conn():
    """Get database connection with retry logic"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn = dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    
    # Retry logic - 3 attempts with increasing delays
    for attempt in range(3):
        try:
            conn = psycopg2.connect(dsn)
            if attempt > 0:
                print(f"тo. Database connection successful on attempt {attempt + 1}")
            return conn
        except psycopg2.OperationalError as e:
            if attempt < 2:  # Not the last attempt
                wait_time = (attempt + 1) * 3  # 3s, 6s
                print(f"тsая,? Database connection failed (attempt {attempt + 1}/3), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"т?O Database connection failed after 3 attempts")
                raise


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
              county TEXT NOT NULL DEFAULT 'duval',
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
            CREATE INDEX IF NOT EXISTS idx_scrape_jobs_county ON scrape_jobs(county);
            """)
            
            # Properties table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS properties (
              id SERIAL PRIMARY KEY,
              parcel TEXT UNIQUE NOT NULL,
              county TEXT NOT NULL DEFAULT 'duval',
              stage TEXT NOT NULL,
              certificate_number TEXT,
              certificate_year INT,
              certificate_age_months INT,
              owner TEXT,
              owner_address TEXT,
              address TEXT,
              city TEXT,
              zip TEXT,
              deed_status TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county);
            CREATE INDEX IF NOT EXISTS idx_properties_stage ON properties(stage);
            CREATE INDEX IF NOT EXISTS idx_properties_verified ON properties(last_verified_at DESC);
            """)
            
            # Property history
            cur.execute("""
            CREATE TABLE IF NOT EXISTS property_history (
              id SERIAL PRIMARY KEY,
              parcel TEXT NOT NULL,
              county TEXT NOT NULL DEFAULT 'duval',
              total_due NUMERIC(12,2),
              delinquent_amount NUMERIC(12,2),
              stage TEXT,
              snapshot_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_history_parcel ON property_history(parcel);
            CREATE INDEX IF NOT EXISTS idx_history_county ON property_history(county);
            CREATE INDEX IF NOT EXISTS idx_history_date ON property_history(snapshot_date DESC);
            """)
            
            # Tax deed notices
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tax_deed_notices (
              id SERIAL PRIMARY KEY,
              parcel TEXT NOT NULL,
              county TEXT NOT NULL DEFAULT 'duval',
              doc_number TEXT,
              recorded_date TEXT,
              party_names TEXT,
              legal_description TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ntd_parcel ON tax_deed_notices(parcel);
            CREATE INDEX IF NOT EXISTS idx_ntd_county ON tax_deed_notices(county);
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


@app.get("/va")
@app.get("/va/")
@app.get("/va.html")
@app.get("/va_portal.html")
def va_portal_page():
    """Serve VA portal (no auth required - handles login internally)"""
    return send_from_directory(BASE_DIR, "va_portal.html")


@app.get("/pricing")
@app.get("/pricing/")
@app.get("/pricing.html")
@app.get("/pricing_admin.html")
def pricing_admin_page():
    """Serve pricing management page"""
    u = require_login(admin=True)
    if not u:
        return redirect("/login")
    return send_from_directory(BASE_DIR, "pricing_admin.html")


@app.get("/dashboard")
@app.get("/dashboard/")
@app.get("/dashboard.html")
@app.get("/user_dashboard.html")
def user_dashboard_page():
    """Serve user dashboard"""
    u = require_login(admin=False)  # Regular users only
    if not u:
        return redirect("/login")
    return send_from_directory(BASE_DIR, "user_dashboard.html")


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
    resp.set_cookie(COOKIE_NAME, token, expires=expires, httponly=True, secure=True, samesite="Lax")
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


# ========== PASSWORD RESET API ==========

@app.post("/api/forgot-password")
def api_forgot_password():
    """Request password reset - generates token"""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    
    if not email:
        return jsonify({"ok": False, "error": "Email required"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Check if email exists in users table
                cur.execute("SELECT email, is_admin FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
                
                # Check if email exists in va_users table
                cur.execute("SELECT email FROM va_users WHERE email = %s", (email,))
                va_user = cur.fetchone()
                
                if not user and not va_user:
                    # Don't reveal if email exists or not (security best practice)
                    return jsonify({
                        "ok": True, 
                        "message": "If that email exists, a reset link has been sent"
                    })
                
                # Determine user type
                if user:
                    user_type = "admin" if user["is_admin"] else "user"
                else:
                    user_type = "va"
                
                # Generate reset token (valid for 1 hour)
                token = secrets.token_urlsafe(32)
                expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
                
                # Store token
                cur.execute("""
                    INSERT INTO password_reset_tokens (email, token, user_type, expires_at)
                    VALUES (%s, %s, %s, %s)
                """, (email, token, user_type, expires_at))
                
                conn.commit()
                
                reset_url = f"https://real-estate-intel.onrender.com/reset-password?token={token}"
                
                print(f"dY"? Password reset requested for {email}")
                print(f"dY" Reset URL: {reset_url}")
                
                # TODO: Send email with reset link in production
                
                return jsonify({
                    "ok": True,
                    "message": "If that email exists, a reset link has been sent",
                    # DEBUG ONLY - Remove in production
                    "debug_token": token,
                    "debug_url": reset_url
                })
    
    except Exception as e:
        print(f"т?O Forgot password error: {e}")
        return jsonify({"ok": False, "error": "Failed to process request"}), 500


@app.post("/api/reset-password")
def api_reset_password():
    """Reset password using token"""
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    new_password = (data.get("password") or "").strip()
    
    if not token or not new_password:
        return jsonify({"ok": False, "error": "Token and password required"}), 400
    
    if len(new_password) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Validate token
                cur.execute("""
                    SELECT email, user_type, expires_at, used
                    FROM password_reset_tokens
                    WHERE token = %s
                """, (token,))
                
                reset_request = cur.fetchone()
                
                if not reset_request:
                    return jsonify({"ok": False, "error": "Invalid reset token"}), 400
                
                if reset_request["used"]:
                    return jsonify({"ok": False, "error": "This reset link has already been used"}), 400
                
                if reset_request["expires_at"] < datetime.now(timezone.utc):
                    return jsonify({"ok": False, "error": "This reset link has expired"}), 400
                
                # Generate new password hash
                new_hash = generate_password_hash(new_password)
                
                # Update password based on user type
                email = reset_request["email"]
                user_type = reset_request["user_type"]
                
                if user_type in ["admin", "user"]:
                    cur.execute("UPDATE users SET pw_hash = %s WHERE email = %s", (new_hash, email))
                else:  # va
                    cur.execute("UPDATE va_users SET password_hash = %s WHERE email = %s", (new_hash, email))
                
                # Mark token as used
                cur.execute("UPDATE password_reset_tokens SET used = TRUE WHERE token = %s", (token,))
                
                conn.commit()
                
                print(f"тo. Password reset successful for {email} ({user_type})")
                
                return jsonify({
                    "ok": True,
                    "message": "Password reset successful! You can now log in.",
                    "redirect": "/login"
                })
    
    except Exception as e:
        print(f"т?O Reset password error: {e}")
        return jsonify({"ok": False, "error": "Failed to reset password"}), 500


@app.get("/api/verify-reset-token/<token>")
def api_verify_reset_token(token):
    """Verify if a reset token is valid"""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT email, user_type, expires_at, used
                    FROM password_reset_tokens
                    WHERE token = %s
                """, (token,))
                
                reset_request = cur.fetchone()
                
                if not reset_request:
                    return jsonify({"ok": False, "valid": False, "error": "Invalid token"})
                
                if reset_request["used"]:
                    return jsonify({"ok": False, "valid": False, "error": "Token already used"})
                
                if reset_request["expires_at"] < datetime.now(timezone.utc):
                    return jsonify({"ok": False, "valid": False, "error": "Token expired"})
                
                return jsonify({
                    "ok": True,
                    "valid": True,
                    "email": reset_request["email"]
                })
    
    except Exception as e:
        print(f"т?O Verify token error: {e}")
        return jsonify({"ok": False, "valid": False, "error": "Failed to verify"}), 500


# ========== COUNTY API ==========

@app.get("/api/counties")
def api_counties():
    """Get list of available counties"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    return jsonify({
        "ok": True,
        "counties": AVAILABLE_COUNTIES
    })


# ========== SCRAPING API ==========

@app.post("/api/trigger_scrape")
@app.post("/api/trigger_scrape/<county>")
def api_trigger_scrape(county="duval"):
    """Manually trigger a scrape job (admin only)"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Validate county
    county_config = next((c for c in AVAILABLE_COUNTIES if c["code"] == county), None)
    if not county_config:
        return jsonify({"ok": False, "error": f"Unknown county: {county}"}), 400
    
    if not county_config["enabled"]:
        return jsonify({"ok": False, "error": f"{county_config['name']} is not enabled yet"}), 400
    
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
            "message": f"Scrape job started for {county_config['name']} County"
        })
    
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/scrape_jobs")
@app.get("/api/scrape_jobs/<county>")
def api_scrape_jobs(county=None):
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
            if county:
                cur.execute("""
                SELECT * FROM scrape_jobs
                WHERE county = %s
                ORDER BY created_at DESC
                LIMIT %s
                """, (county, limit))
            else:
                cur.execute("""
                SELECT * FROM scrape_jobs
                ORDER BY created_at DESC
                LIMIT %s
                """, (limit,))
            
            jobs = cur.fetchall()
    
    return jsonify({"ok": True, "jobs": jobs})


@app.get("/api/properties")
@app.get("/api/properties/<county>")
def api_properties(county=None):
    """Get properties from database"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Filters
    stage = request.args.get("stage")
    min_due = request.args.get("min_due")
    zip_code = request.args.get("zip")
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "tax")
    
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = max(1, min(100000, int(request.args.get("page_size", "50"))))
    except:
        page = 1
        page_size = 50
    
    # Build query
    where_clauses = []
    params = []
    
    if county:
        where_clauses.append("county = %s")
        params.append(county)
    
    if category:
        where_clauses.append("category = %s")
        params.append(category)
    
    if stage:
        where_clauses.append("stage = %s")
        params.append(stage)
    
    if min_due:
        try:
            where_clauses.append("current_total_due >= %s")
            params.append(float(min_due))
        except:
            pass
    
    if search:
        # Search in address, parcel, zip, or owner
        where_clauses.append("(LOWER(address) LIKE %s OR LOWER(parcel) LIKE %s OR LOWER(zip) LIKE %s OR LOWER(owner) LIKE %s)")
        search_term = f"%{search.lower()}%"
        params.extend([search_term, search_term, search_term, search_term])
    
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


@app.get("/api/health")
def api_health():
    """Health check endpoint - checks database connectivity"""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 503


@app.get("/api/stats")
@app.get("/api/stats/<county>")
def api_stats(county=None):
    """Get overall statistics"""
    u = require_login(admin=False)  # Allow all logged-in users
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Build county filter
            county_filter = "WHERE county = %s" if county else ""
            county_params = [county] if county else []
            
            # Overall counts
            cur.execute(f"""
            SELECT 
                COUNT(*) as total_properties,
                COUNT(*) FILTER (WHERE stage = 'sweet_spot') as sweet_spot_count,
                COUNT(*) FILTER (WHERE has_tax_deed_notice = TRUE) as with_ntd,
                SUM(current_total_due) as total_amount_due
            FROM properties
            {county_filter}
            """, county_params)
            overall = cur.fetchone()
            
            # By stage
            cur.execute(f"""
            SELECT stage, COUNT(*) as count
            FROM properties
            {county_filter}
            GROUP BY stage
            ORDER BY count DESC
            """, county_params)
            by_stage = cur.fetchall()
            
            # Recent jobs
            cur.execute(f"""
            SELECT status, COUNT(*) as count
            FROM scrape_jobs
            WHERE created_at > NOW() - INTERVAL '7 days'
            {("AND county = %s" if county else "")}
            GROUP BY status
            """, county_params)
            recent_jobs = cur.fetchall()
    
    return jsonify({
        "ok": True,
        "overall": overall,
        "by_stage": by_stage,
        "recent_jobs": recent_jobs
    })


# ========== FILE UPLOAD API ==========

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def classify_stage_from_data(row):
    """Classify property stage based on available data"""
    has_ntd = row.get('has_tax_deed_notice', False)
    total_due = float(row.get('current_total_due', 0) or 0)
    
    if has_ntd:
        return 'tax_deed_filed'
    elif total_due > 5000:
        return 'sweet_spot'
    elif total_due > 0:
        return 'pre_lien'
    else:
        return 'current'


@app.post("/api/upload_properties")
@app.post("/api/upload_properties/<county>")
def api_upload_properties(county="duval"):
    """Upload property data from CSV file (lightweight, no pandas)"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"ok": False, "error": "No file selected"}), 400
    
    # Only support CSV for now (Excel requires too much memory on free tier)
    if not file.filename.endswith('.csv'):
        return jsonify({"ok": False, "error": "Please upload CSV format only (.csv). Excel files use too much memory on free tier."}), 400
    
    try:
        # Read CSV file
        file_content = file.read().decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(file_content))
        
        # Get all rows
        rows = list(csv_reader)
        
        if not rows:
            return jsonify({"ok": False, "error": "File is empty"}), 400
        
        # Column mapping (flexible - handles different column names)
        column_map = {
            'parcel': ['parcel', 'parcel_id', 'parcel_number', 'parcel id', 'account', 'account_no'],
            'owner': ['owner', 'owner_name', 'name', 'taxpayer', 'taxpayer_name'],
            'address': ['address', 'property_address', 'situs_address', 'situs address', 'location'],
            'city': ['city', 'situs_city'],
            'zip': ['zip', 'zip_code', 'zipcode', 'situs_zip'],
            'current_total_due': ['total_due', 'amount_due', 'total_amount_due', 'amount', 'balance'],
            'face_amount': ['face_amount', 'certificate_amount', 'face amount'],
            'certificate_number': ['certificate', 'cert_number', 'certificate_number', 'cert number', 'cert_no'],
            'has_tax_deed_notice': ['tax_deed_notice', 'ntd', 'notice', 'has_notice'],
        }
        
        # Normalize available columns
        available_cols = [col.strip().lower() for col in rows[0].keys()]
        
        # Map columns
        mapped_cols = {}
        for target_col, possible_names in column_map.items():
            for name in possible_names:
                if name in available_cols:
                    # Find original case column name
                    for orig_col in rows[0].keys():
                        if orig_col.strip().lower() == name:
                            mapped_cols[target_col] = orig_col
                            break
                    break
        
        # Check if we have at least parcel column
        if 'parcel' not in mapped_cols:
            return jsonify({
                "ok": False, 
                "error": f"Could not find parcel column. Available columns: {', '.join(rows[0].keys())}"
            }), 400
        
        imported = 0
        duplicates = 0
        errors = []
        
        with db_conn() as conn:
            with conn.cursor() as cur:
                for idx, row in enumerate(rows):
                    try:
                        # Get parcel (required)
                        parcel = str(row.get(mapped_cols['parcel'], '')).strip()
                        if not parcel or parcel.lower() == 'nan':
                            continue
                        
                        # Get other fields (optional)
                        owner = str(row.get(mapped_cols.get('owner', ''), '')).strip()
                        address = str(row.get(mapped_cols.get('address', ''), '')).strip()
                        city = str(row.get(mapped_cols.get('city', ''), '')).strip()
                        zip_code = str(row.get(mapped_cols.get('zip', ''), '')).strip()
                        
                        # Financial data
                        try:
                            total_due_str = str(row.get(mapped_cols.get('current_total_due', ''), ''))
                            total_due = float(total_due_str.replace('$', '').replace(',', '')) if total_due_str else None
                        except:
                            total_due = None
                        
                        try:
                            face_str = str(row.get(mapped_cols.get('face_amount', ''), ''))
                            face_amount = float(face_str.replace('$', '').replace(',', '')) if face_str else None
                        except:
                            face_amount = None
                        
                        # Certificate data
                        cert_number = str(row.get(mapped_cols.get('certificate_number', ''), '')).strip()
                        
                        # Tax deed notice
                        has_ntd = False
                        if 'has_tax_deed_notice' in mapped_cols:
                            ntd_val = str(row.get(mapped_cols['has_tax_deed_notice'], '')).lower()
                            has_ntd = ntd_val in ['true', 'yes', '1', 't', 'y']
                        
                        # Classify stage
                        stage = classify_stage_from_data({
                            'has_tax_deed_notice': has_ntd,
                            'current_total_due': total_due
                        })
                        
                        # Insert or update
                        cur.execute("""
                            INSERT INTO properties (
                                parcel, county, stage, owner, address, city, zip,
                                current_total_due, face_amount, certificate_number,
                                has_tax_deed_notice, last_verified_at, created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW()
                            )
                            ON CONFLICT (parcel) DO UPDATE SET
                                owner = COALESCE(NULLIF(EXCLUDED.owner, ''), properties.owner),
                                address = COALESCE(NULLIF(EXCLUDED.address, ''), properties.address),
                                city = COALESCE(NULLIF(EXCLUDED.city, ''), properties.city),
                                zip = COALESCE(NULLIF(EXCLUDED.zip, ''), properties.zip),
                                current_total_due = COALESCE(EXCLUDED.current_total_due, properties.current_total_due),
                                face_amount = COALESCE(EXCLUDED.face_amount, properties.face_amount),
                                certificate_number = COALESCE(NULLIF(EXCLUDED.certificate_number, ''), properties.certificate_number),
                                has_tax_deed_notice = EXCLUDED.has_tax_deed_notice OR properties.has_tax_deed_notice,
                                stage = EXCLUDED.stage,
                                last_verified_at = NOW(),
                                updated_at = NOW()
                        """, (parcel, county, stage, owner, address, city, zip_code, 
                              total_due, face_amount, cert_number, has_ntd))
                        
                        imported += 1
                            
                    except Exception as e:
                        errors.append(f"Row {idx + 2}: {str(e)}")
                        if len(errors) > 10:
                            errors.append("... and more errors")
                            break
                        continue
                
                conn.commit()
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "duplicates": duplicates,
            "total_rows": len(rows),
            "errors": errors[:10] if errors else []
        })
        
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"Failed to process file: {str(e)}"
        }), 500


# ========== BATCH UPLOAD FOR LARGE FILES ==========

def classify_stage_from_cert(cert_status, issued_date_str, deed_status):
    """Classify based on tax certificate data (county-taxes.net format)"""
    try:
        # Parse issued date - handles multiple formats from Excel
        years_old = 0
        issued_date_str = str(issued_date_str or '').strip()
        try:
            if '-' in issued_date_str:
                # Excel datetime format: '2023-05-24 12:00:00' or '2023-05-24'
                parsed = datetime.strptime(issued_date_str[:10], '%Y-%m-%d')
            elif '/' in issued_date_str:
                # String format: '5/24/2023'
                parsed = datetime.strptime(issued_date_str, '%m/%d/%Y')
            else:
                parsed = None
            if parsed:
                years_old = (datetime.now() - parsed).days / 365.25
        except:
            years_old = 0
        
        # Normalize statuses
        cert_status_clean = str(cert_status or '').strip().upper()
        deed_status_clean = str(deed_status or '').strip().upper()
        
        # Redeemed = paid off
        if 'REDEEMED' in cert_status_clean:
            return 'current'
        
        # Cancelled
        if 'CANCELED' in deed_status_clean or 'CANCELLED' in deed_status_clean:
            return 'current'
        
        # LAS = Lands Available = Failed auction (HOTTEST LEAD!)
        if deed_status_clean == 'LAS':
            return 'auction_failed'
        
        # Applied or Certified = Tax deed filed
        if 'APPLIED' in deed_status_clean or 'CERTIFIED' in deed_status_clean:
            return 'tax_deed_filed'
        
        # Any other real deed status
        has_deed = deed_status_clean and deed_status_clean not in ['-- NONE --', 'NONE', 'NULL', '', 'N/A']
        if has_deed:
            return 'tax_deed_filed'
        
        # Classify by certificate age (if we reach here, certificate is unpaid with no deed)
        if years_old >= 2 and years_old <= 5:
            return 'sweet_spot'
        elif years_old > 5:
            return 'danger_zone'
        elif years_old >= 1:
            return 'pre_lien'
        else:
            # Certificate exists but less than 1 year old
            return 'unpaid_new'
    except Exception as e:
        print(f"тsая,? Classification error: {e}, issued_date={issued_date_str}")
        return 'pre_lien'


def process_excel_batch(rows, county):
    """Process batch with smart parcel grouping - takes highest Face Amount per parcel"""
    imported = 0
    errors = []
    skipped_filtered = 0  # LAS + Redeemed certificates
    
    # Group rows by parcel and keep the one with highest Face Amount
    parcel_groups = {}
    
    for row in rows:
        try:
            parcel = str(row.get('parcel', '')).strip()
            if not parcel or parcel.upper() in ['NONE', 'NULL', '']:
                continue
            
            # Skip LAS (Lands Available) - getting these from elsewhere
            deed_status_check = str(row.get('Deed Status', '')).strip().upper()
            if deed_status_check == 'LAS':
                skipped_filtered += 1
                continue
            
            # Skip REDEEMED certificates - no opportunity
            cert_status_check = str(row.get('Cert Status', '')).strip().upper()
            if 'REDEEMED' in cert_status_check:
                skipped_filtered += 1
                continue
            
            # Parse Face Amount
            try:
                face_str = str(row.get('Face Amount', '')).replace('$', '').replace(',', '').strip()
                face_amount = float(face_str) if face_str and face_str.upper() not in ['NONE', 'NULL', ''] else 0
            except:
                face_amount = 0
            
            # Keep row with highest Face Amount per parcel (bold total row)
            if parcel not in parcel_groups or face_amount > parcel_groups[parcel].get('face_amount', 0):
                parcel_groups[parcel] = {'row': row, 'face_amount': face_amount}
        except:
            continue
    
    # Process all parcels in a single transaction for speed
    print(f"dY"ж Grouped into {len(parcel_groups)} unique parcels (from {len(rows)} rows)")
    
    with db_conn() as conn:
        with conn.cursor() as cur:
            for parcel, data in parcel_groups.items():
                try:
                    row = data['row']
                    face_amount = data['face_amount']
                    
                    # Extract all columns cleanly
                    owner_name = str(row.get('Owner Name', '') or '').strip()
                    owner_address = str(row.get('Owner Address', '') or '').strip()
                    property_address = str(row.get('Property Address', '') or '').strip()
                    deed_status_raw = str(row.get('Deed Status', '') or '').strip()
                    cert_status = str(row.get('Cert Status', '') or '').strip()
                    issued_date = str(row.get('Issued Date', '') or '').strip()
                    
                    # Clean cert number - remove .0 suffix Excel adds
                    cert_raw = str(row.get('Cert #', '') or '').strip()
                    try:
                        cert_number = str(int(float(cert_raw))) if cert_raw and cert_raw not in ['', 'None', 'NULL'] else None
                    except:
                        cert_number = cert_raw if cert_raw else None
                    
                    # Clean face amount
                    face_amount_save = float(face_amount) if face_amount and face_amount > 0 else None
                    
                    # Normalize deed status - treat "-- None --" and empty as no deed
                    deed_status_upper = deed_status_raw.upper()
                    has_real_deed_status = bool(
                        deed_status_upper and 
                        deed_status_upper not in ['-- NONE --', 'NONE', 'NULL', '', 'N/A']
                    )
                    
                    # Save deed_status as None if no real deed status
                    deed_status_save = deed_status_raw if has_real_deed_status else None
                    
                    # has_ntd MUST be True or False - never empty string or None
                    has_ntd = True if has_real_deed_status else False
                    
                    # Parse issued date - handles Excel datetime and string formats
                    years_old = 0
                    try:
                        if '-' in issued_date:
                            parsed_date = datetime.strptime(issued_date[:10], '%Y-%m-%d')
                        elif '/' in issued_date:
                            parsed_date = datetime.strptime(issued_date, '%m/%d/%Y')
                        else:
                            parsed_date = None
                        if parsed_date:
                            years_old = (datetime.now() - parsed_date).days / 365.25
                    except:
                        years_old = 0
                    
                    # Classify stage
                    stage = classify_stage_from_cert(cert_status, issued_date, deed_status_raw)
                    
                    # Insert/update in single transaction
                    cur.execute("""
                        INSERT INTO properties (
                            parcel, county, stage, owner, owner_address, address,
                            certificate_number, deed_status, face_amount, current_total_due,
                            has_tax_deed_notice, last_verified_at, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW()
                        )
                        ON CONFLICT (parcel) DO UPDATE SET
                            owner = EXCLUDED.owner,
                            owner_address = EXCLUDED.owner_address,
                            address = EXCLUDED.address,
                            certificate_number = EXCLUDED.certificate_number,
                            deed_status = EXCLUDED.deed_status,
                            face_amount = EXCLUDED.face_amount,
                            current_total_due = EXCLUDED.current_total_due,
                            has_tax_deed_notice = EXCLUDED.has_tax_deed_notice,
                            stage = EXCLUDED.stage,
                            last_verified_at = NOW(),
                            updated_at = NOW()
                    """, (
                        parcel, county, stage,
                        owner_name or None,
                        owner_address or None,
                        property_address or None,
                        cert_number,
                        deed_status_save,
                        face_amount_save,
                        face_amount_save,
                        has_ntd
                    ))
                    
                    imported += 1
                    
                except Exception as e:
                    error_msg = f"т?O ERROR for parcel {parcel}: {str(e)}"
                    print(error_msg)
                    errors.append(error_msg)
                    if len(errors) > 20:
                        print(f"тsая,? Too many errors, stopping at 20. Total errors so far: {len(errors)}")
                        break
            
            # Commit all changes at once
            conn.commit()
    
    return {'imported': imported, 'errors': errors, 'skipped_filtered': skipped_filtered}


@app.post("/api/upload_batch/<county>")
def api_upload_batch(county="duval"):
    """Batch upload for large files (processes 1000 rows at a time) - supports Excel, CSV, TSV"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    
    file = request.files['file']
    
    is_excel = file.filename.endswith(('.xlsx', '.xls'))
    is_csv = file.filename.endswith('.csv')
    
    if not is_excel and not is_csv:
        return jsonify({"ok": False, "error": "Please upload .xlsx, .xls, or .csv file"}), 400
    
    if is_excel and not EXCEL_SUPPORT:
        return jsonify({"ok": False, "error": "openpyxl not installed"}), 500
    
    try:
        BATCH_SIZE = 1000  # Increased back to 1000 with single-connection optimization
        imported = 0
        skipped = 0
        errors = []
        row_count = 0
        
        if is_excel:
            # Excel file processing
            print(f"dY"S Starting Excel upload for {county}...")
            wb = load_workbook(file.stream, read_only=True, data_only=True)
            ws = wb.active
            
            # Get headers
            headers = [str(cell.value).strip() if cell.value else '' for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            
            batch = []
            
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_dict = dict(zip(headers, [str(cell) if cell else '' for cell in row]))
                
                if row_dict.get('parcel'):
                    batch.append(row_dict)
                    row_count += 1
                else:
                    skipped += 1
                
                if len(batch) >= BATCH_SIZE:
                    print(f"   Processing rows {row_count - BATCH_SIZE + 1} to {row_count}...")
                    result = process_excel_batch(batch, county)
                    imported += result['imported']
                    errors.extend(result['errors'])
                    batch = []
            
            if batch:
                print(f"   Processing final {len(batch)} rows...")
                result = process_excel_batch(batch, county)
                imported += result['imported']
                errors.extend(result['errors'])
            
            wb.close()
            print(f"тo. Upload complete: {imported} imported, {skipped} skipped")
        
        else:
            # CSV/TSV file processing
            print(f"dY"S Starting CSV upload for {county}...")
            file_content = file.read().decode('utf-8', errors='ignore')
            
            # Detect delimiter (tab or comma)
            first_line = file_content.split('\n')[0]
            delimiter = '\t' if '\t' in first_line else ','
            
            csv_reader = csv.DictReader(io.StringIO(file_content), delimiter=delimiter)
            
            batch = []
            
            for row in csv_reader:
                if row.get('parcel'):
                    batch.append(row)
                    row_count += 1
                else:
                    skipped += 1
                
                if len(batch) >= BATCH_SIZE:
                    print(f"   Processing rows {row_count - BATCH_SIZE + 1} to {row_count}...")
                    result = process_excel_batch(batch, county)
                    imported += result['imported']
                    errors.extend(result['errors'])
                    batch = []
            
            if batch:
                print(f"   Processing final {len(batch)} rows...")
                result = process_excel_batch(batch, county)
                imported += result['imported']
                errors.extend(result['errors'])
            
            print(f"тo. Upload complete: {imported} imported, {skipped} skipped")
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "skipped_filtered": result.get('skipped_filtered', 0) if 'result' in locals() else 0,
            "errors": errors[:10]
        })
    
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Initialize database tables on startup - DISABLED TEMPORARILY
# All tables already exist, commenting out to avoid init error
# try:
#     db_init()
#     print("тo. Database initialized successfully!")
# except Exception as e:
#     import traceback
#     print(f"тsая,?  Database init failed: {e}")
#     print(f"тsая,?  Full traceback: {traceback.format_exc()}")

print("тsая,?  db_init() disabled - tables already exist in database")


# ========== STRIPE PAYMENT API ==========

@app.post("/api/stripe/create-payment-intent")
def create_payment_intent():
    """Create Stripe payment intent for service request"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    service_type = data.get('service_type')
    parcel = data.get('parcel')
    property_id = data.get('property_id')
    tip_amount = float(data.get('tip_amount', 0))
    
    if not service_type or not parcel:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get pricing
                cur.execute("""
                    SELECT price_charged, service_name
                    FROM service_pricing
                    WHERE service_type = %s AND active = TRUE
                """, (service_type,))
                
                pricing = cur.fetchone()
                
                if not pricing:
                    return jsonify({"ok": False, "error": "Service not available"}), 404
                
                # Calculate total amount (service + tip)
                service_amount = float(pricing['price_charged'])
                total_amount = service_amount + tip_amount
                amount_cents = int(total_amount * 100)  # Convert to cents
                
                payment_intent = stripe.PaymentIntent.create(
                    amount=amount_cents,
                    currency='usd',
                    metadata={
                        'service_type': service_type,
                        'parcel': parcel,
                        'property_id': property_id or '',
                        'user_email': u['email'],
                        'tip_amount': str(tip_amount)
                    },
                    description=f"{pricing['service_name']} for {parcel}" + (f" + ${tip_amount:.2f} tip" if tip_amount > 0 else "")
                )
                
                return jsonify({
                    "ok": True,
                    "clientSecret": payment_intent.client_secret,
                    "amount": total_amount
                })
    
    except Exception as e:
        print(f"т?O Create payment intent error: {e}")
        return jsonify({"ok": False, "error": "Payment failed"}), 500


@app.post("/api/stripe/webhook")
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    
    # Handle payment_intent.succeeded
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        
        try:
            with db_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Get metadata
                    metadata = payment_intent.get('metadata', {})
                    service_type = metadata.get('service_type')
                    parcel = metadata.get('parcel')
                    property_id = metadata.get('property_id')
                    user_email = metadata.get('user_email')
                    tip_amount = float(metadata.get('tip_amount', 0))
                    
                    # Get pricing
                    cur.execute("""
                        SELECT price_charged, va_payout
                        FROM service_pricing
                        WHERE service_type = %s
                    """, (service_type,))
                    
                    pricing = cur.fetchone()
                    
                    # Get property details
                    cur.execute("""
                        SELECT address, owner
                        FROM properties
                        WHERE parcel = %s
                    """, (parcel,))
                    
                    prop = cur.fetchone()
                    
                    # Create service request
                    cur.execute("""
                        INSERT INTO service_requests (
                            service_type,
                            property_id,
                            parcel,
                            property_address,
                            owner_name,
                            user_email,
                            status,
                            amount_charged,
                            va_payout,
                            tip_amount,
                            stripe_payment_intent_id,
                            payment_status,
                            paid_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, 'paid', NOW())
                        RETURNING id
                    """, (
                        service_type,
                        int(property_id) if property_id else None,
                        parcel,
                        prop['address'] if prop else None,
                        prop['owner'] if prop else None,
                        user_email,
                        pricing['price_charged'],
                        pricing['va_payout'],
                        tip_amount,
                        payment_intent['id']
                    ))
                    
                    conn.commit()
                    
                    # Track spending for rewards system
                    total_amount = float(pricing['price_charged']) + tip_amount
                    update_user_rewards_tracking(user_email, amount_spent=total_amount, time_seconds=0)
                    
                    print(f"тo. Service request created from payment: {payment_intent['id']}")
        
        except Exception as e:
            print(f"т?O Webhook processing error: {e}")
            return jsonify({"error": "Processing failed"}), 500
    
    return jsonify({"ok": True})


@app.get("/api/stripe/config")
def get_stripe_config():
    """Get Stripe publishable key"""
    return jsonify({
        "publishableKey": STRIPE_PUBLISHABLE_KEY
    })


# ========== USER SERVICE REQUEST API ==========

@app.get("/api/pricing/active")
def api_get_active_pricing():
    """Get active service pricing for users"""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT service_type, service_name, service_description, price_charged, display_order
                    FROM service_pricing
                    WHERE active = TRUE
                    ORDER BY display_order, service_name
                """)
                
                pricing = cur.fetchall()
                
                return jsonify({"ok": True, "pricing": pricing})
    
    except Exception as e:
        print(f"т?O Get active pricing error: {e}")
        return jsonify({"ok": False, "error": "Failed to load pricing"}), 500


@app.post("/api/user/service-request")
def api_user_create_service_request():
    """User creates a service request"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    property_id = data.get('property_id')
    parcel = data.get('parcel')
    service_type = data.get('service_type')
    
    if not parcel or not service_type:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get pricing
                cur.execute("""
                    SELECT price_charged, va_payout, service_name
                    FROM service_pricing
                    WHERE service_type = %s AND active = TRUE
                """, (service_type,))
                
                pricing = cur.fetchone()
                
                if not pricing:
                    return jsonify({"ok": False, "error": "Service not available"}), 404
                
                # Get property details
                cur.execute("""
                    SELECT address, owner
                    FROM properties
                    WHERE parcel = %s
                """, (parcel,))
                
                prop = cur.fetchone()
                
                # Create service request
                cur.execute("""
                    INSERT INTO service_requests (
                        service_type,
                        property_id,
                        parcel,
                        property_address,
                        owner_name,
                        user_email,
                        status,
                        amount_charged,
                        va_payout
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                    RETURNING id
                """, (
                    service_type,
                    property_id,
                    parcel,
                    prop['address'] if prop else None,
                    prop['owner'] if prop else None,
                    u['email'],
                    pricing['price_charged'],
                    pricing['va_payout']
                ))
                
                request_id = cur.fetchone()['id']
                conn.commit()
                
                return jsonify({
                    "ok": True,
                    "request_id": request_id,
                    "message": "Service request created"
                })
    
    except Exception as e:
        print(f"т?O Create service request error: {e}")
        return jsonify({"ok": False, "error": "Failed to create request"}), 500


@app.get("/api/user/service-requests")
def api_user_get_service_requests():
    """Get user's service requests"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM service_requests
                    WHERE user_email = %s
                    ORDER BY created_at DESC
                """, (u['email'],))
                
                requests = cur.fetchall()
                
                return jsonify({"ok": True, "requests": requests})
    
    except Exception as e:
        print(f"т?O Get user service requests error: {e}")
        return jsonify({"ok": False, "error": "Failed to load requests"}), 500


# ========== ADMIN SERVICE REQUEST MANAGEMENT ==========

@app.get("/api/admin/pricing")
def api_admin_get_pricing():
    """Get service pricing"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM service_pricing
                    ORDER BY display_order, service_name
                """)
                
                pricing = cur.fetchall()
                
                return jsonify({"ok": True, "pricing": pricing})
    
    except Exception as e:
        print(f"т?O Get pricing error: {e}")
        return jsonify({"ok": False, "error": "Failed to load pricing"}), 500


@app.put("/api/admin/pricing/<int:pricing_id>")
def api_admin_update_pricing(pricing_id):
    """Update service pricing"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    price_charged = data.get('price_charged')
    va_payout = data.get('va_payout')
    
    if price_charged is None or va_payout is None:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    
    if float(price_charged) < float(va_payout):
        return jsonify({"ok": False, "error": "Price charged must be >= VA payout"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE service_pricing
                    SET price_charged = %s,
                        va_payout = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (price_charged, va_payout, pricing_id))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Pricing updated"})
    
    except Exception as e:
        print(f"т?O Update pricing error: {e}")
        return jsonify({"ok": False, "error": "Failed to update pricing"}), 500


@app.put("/api/admin/pricing/<int:pricing_id>/toggle")
def api_admin_toggle_pricing(pricing_id):
    """Toggle service active status"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    active = data.get('active', True)
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE service_pricing
                    SET active = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (active, pricing_id))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Status updated"})
    
    except Exception as e:
        print(f"т?O Toggle pricing error: {e}")
        return jsonify({"ok": False, "error": "Failed to update status"}), 500


@app.get("/api/admin/service-requests")
def api_admin_get_service_requests():
    """Get service requests for admin review"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    status = request.args.get('status', 'submitted')
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM service_requests
                    WHERE status = %s
                    ORDER BY submitted_at DESC
                """, (status,))
                
                requests = cur.fetchall()
                
                return jsonify({"ok": True, "requests": requests})
    
    except Exception as e:
        print(f"т?O Admin service requests error: {e}")
        return jsonify({"ok": False, "error": "Failed to load requests"}), 500


@app.post("/api/admin/service-requests/<int:request_id>/approve")
def api_admin_approve_request(request_id):
    """Approve service request - mark VA for payment and complete request"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get request details
                cur.execute("""
                    SELECT claimed_by_va_id, va_payout, parcel, phone, email, notes
                    FROM service_requests
                    WHERE id = %s
                """, (request_id,))
                
                req = cur.fetchone()
                
                if not req:
                    return jsonify({"ok": False, "error": "Request not found"}), 404
                
                # Update request status to completed
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'completed',
                        completed_at = NOW(),
                        reviewed_by_admin = %s,
                        reviewed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                """, (u['email'], request_id))
                
                # Update VA stats and mark for payment
                if req['claimed_by_va_id']:
                    cur.execute("""
                        UPDATE va_users
                        SET total_completed = total_completed + 1,
                            total_earned = total_earned + %s
                        WHERE id = %s
                    """, (req['va_payout'], req['claimed_by_va_id']))
                
                # Update property with skiptracing results
                if req['parcel']:
                    cur.execute("""
                        UPDATE properties
                        SET skiptrace_status = 'completed',
                            skiptrace_phone = %s,
                            skiptrace_email = %s,
                            skiptrace_notes = %s,
                            skiptrace_completed_at = NOW()
                        WHERE parcel = %s
                    """, (req['phone'], req['email'], req['notes'], req['parcel']))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Request approved"})
    
    except Exception as e:
        print(f"т?O Approve request error: {e}")
        return jsonify({"ok": False, "error": "Failed to approve request"}), 500


@app.post("/api/admin/service-requests/<int:request_id>/reject")
def api_admin_reject_request(request_id):
    """Reject service request - return to queue"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    reason = data.get('reason', 'No reason provided')
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Return to queue
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'pending',
                        claimed_by_va_id = NULL,
                        claimed_by_va_email = NULL,
                        claimed_at = NULL,
                        submitted_at = NULL,
                        rejected_at = NOW(),
                        rejection_reason = %s,
                        reviewed_by_admin = %s,
                        reviewed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                """, (reason, u['email'], request_id))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Request rejected and returned to queue"})
    
    except Exception as e:
        print(f"т?O Reject request error: {e}")
        return jsonify({"ok": False, "error": "Failed to reject request"}), 500


@app.post("/api/admin/properties/bulk-upload")
def bulk_upload_properties():
    """Bulk upload properties from CSV"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Admin access required"}), 403
    
    data = request.get_json(silent=True) or {}
    properties = data.get("properties", [])
    
    if not properties:
        return jsonify({"ok": False, "error": "No properties provided"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                inserted = 0
                for prop in properties:
                    # Required field
                    parcel = prop.get("parcel")
                    if not parcel:
                        continue
                    
                    # Optional fields
                    category = prop.get("category", "tax")
                    address = prop.get("address")
                    owner = prop.get("owner")
                    county = prop.get("county", "Duval")
                    zip_code = prop.get("zip")
                    city = prop.get("city")
                    state = prop.get("state", "FL")
                    stage = prop.get("stage", "sweet_spot")
                    current_total_due = prop.get("current_total_due", 0)
                    
                    try:
                        current_total_due = float(current_total_due) if current_total_due else 0
                    except:
                        current_total_due = 0
                    
                    # Insert or update (upsert on parcel)
                    cur.execute("""
                        INSERT INTO properties 
                            (category, parcel, address, owner, county, zip, city, state, stage, current_total_due)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (parcel) DO UPDATE SET
                            category = EXCLUDED.category,
                            address = EXCLUDED.address,
                            owner = EXCLUDED.owner,
                            county = EXCLUDED.county,
                            zip = EXCLUDED.zip,
                            city = EXCLUDED.city,
                            state = EXCLUDED.state,
                            stage = EXCLUDED.stage,
                            current_total_due = EXCLUDED.current_total_due
                    """, (category, parcel, address, owner, county, zip_code, city, state, stage, current_total_due))
                    
                    inserted += 1
                
                conn.commit()
                
        return jsonify({"ok": True, "inserted": inserted})
        
    except Exception as e:
        print(f"т?O Bulk upload error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ========== VA PORTAL API ENDPOINTS ==========

@app.post("/api/va/login")
def api_va_login():
    """VA login endpoint"""
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, email, name, phone, total_completed, total_earned, password_hash, active
                    FROM va_users
                    WHERE email = %s
                """, (email,))
                
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "Invalid credentials"}), 401
                
                if not va['active']:
                    return jsonify({"ok": False, "error": "Account disabled"}), 401
                
                # Check password using werkzeug (bcrypt-compatible)
                if not check_password_hash(va['password_hash'], password):
                    return jsonify({"ok": False, "error": "Invalid credentials"}), 401
                
                # Update last login
                cur.execute("UPDATE va_users SET last_login = NOW() WHERE id = %s", (va['id'],))
                
                # Create session token
                token = secrets.token_urlsafe(32)
                expires = datetime.now(timezone.utc) + timedelta(days=7)
                cur.execute("""
                    INSERT INTO va_sessions (token, va_user_id, expires_at)
                    VALUES (%s, %s, %s)
                """, (token, va['id'], expires))
                
                conn.commit()
                
                # Return VA data (without password hash)
                va_data = dict(va)
                del va_data['password_hash']
                
                # Convert Decimal/string to float for JSON
                va_data['total_earned'] = float(va_data.get('total_earned') or 0)
                
                resp = make_response(jsonify({"ok": True, "va": va_data}))
                resp.set_cookie("va_session", token, expires=expires, httponly=True, secure=True, samesite="Lax")
                return resp
    
    except Exception as e:
        print(f"т?O VA login error: {e}")
        return jsonify({"ok": False, "error": "Login failed"}), 500


@app.get("/api/va/profile")
def api_va_profile():
    """Get VA profile using session cookie"""
    va_session = request.cookies.get("va_session", "")
    
    if not va_session:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get VA from session
                cur.execute("""
                    SELECT v.id, v.email, v.name, v.phone, v.active, v.total_completed, v.total_earned
                    FROM va_sessions s
                    JOIN va_users v ON v.id = s.va_user_id
                    WHERE s.token = %s AND s.expires_at > NOW()
                """, (va_session,))
                
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "Invalid session"}), 401
                
                # Get job counts
                cur.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE status IN ('claimed', 'in_progress')) as active_jobs,
                        COUNT(*) FILTER (WHERE status = 'completed') as completed_jobs
                    FROM service_requests
                    WHERE claimed_by_va_id = %s
                """, (va['id'],))
                
                counts = cur.fetchone()
                
                va_data = dict(va)
                va_data['total_earned'] = float(va_data.get('total_earned') or 0)
                va_data['active_jobs'] = counts['active_jobs'] if counts else 0
                va_data['completed_jobs'] = counts['completed_jobs'] if counts else 0
                
                return jsonify({"ok": True, **va_data})
    
    except Exception as e:
        print(f"т?O VA profile error: {e}")
        return jsonify({"ok": False, "error": "Failed to load profile"}), 500


@app.get("/api/va/jobs/available")
def api_va_jobs_available():
    """Get available jobs for VA using session cookie"""
    va_session = request.cookies.get("va_session", "")
    
    if not va_session:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Verify VA session
                cur.execute("""
                    SELECT va_user_id
                    FROM va_sessions
                    WHERE token = %s AND expires_at > NOW()
                """, (va_session,))
                
                session = cur.fetchone()
                
                if not session:
                    return jsonify({"ok": False, "error": "Invalid session"}), 401
                
                # Get available jobs
                cur.execute("""
                    SELECT sr.*, p.address, p.parcel, p.owner
                    FROM service_requests sr
                    LEFT JOIN property_listings p ON p.id = sr.property_id
                    WHERE sr.status = 'pending'
                    ORDER BY sr.created_at ASC
                    LIMIT 50
                """)
                
                jobs = cur.fetchall()
                
                # Convert to dict and format
                jobs_list = []
                for job in jobs:
                    job_dict = dict(job)
                    # Convert any Decimal to float
                    if 'va_payout' in job_dict and job_dict['va_payout']:
                        job_dict['va_payout'] = float(job_dict['va_payout'])
                    jobs_list.append(job_dict)
                
                return jsonify({"ok": True, "jobs": jobs_list})
    
    except Exception as e:
        print(f"т?O VA available jobs error: {e}")
        return jsonify({"ok": False, "error": "Failed to load jobs"}), 500


@app.get("/api/va/jobs")
def api_va_jobs():
    """Get jobs for VA - available, active, and pending review"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get VA ID
                cur.execute("SELECT id FROM va_users WHERE email = %s AND active = TRUE", (va_email,))
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "VA not found"}), 404
                
                va_id = va['id']
                
                # Available jobs (pending, not claimed by anyone)
                cur.execute("""
                    SELECT * FROM service_requests
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 50
                """)
                available = cur.fetchall()
                
                # Active jobs (claimed by this VA)
                cur.execute("""
                    SELECT * FROM service_requests
                    WHERE claimed_by_va_id = %s 
                    AND status IN ('claimed', 'in_progress')
                    ORDER BY claimed_at DESC
                """, (va_id,))
                active = cur.fetchall()
                
                # Pending review (submitted by this VA)
                cur.execute("""
                    SELECT * FROM service_requests
                    WHERE claimed_by_va_id = %s 
                    AND status = 'submitted'
                    ORDER BY submitted_at DESC
                """, (va_id,))
                pending_review = cur.fetchall()
                
                return jsonify({
                    "ok": True,
                    "available": available,
                    "active": active,
                    "pending_review": pending_review
                })
    
    except Exception as e:
        print(f"т?O VA jobs error: {e}")
        return jsonify({"ok": False, "error": "Failed to load jobs"}), 500


@app.post("/api/va/jobs/<int:job_id>/claim")
def api_va_claim_job(job_id):
    """Claim a job"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get VA
                cur.execute("SELECT id, name FROM va_users WHERE email = %s AND active = TRUE", (va_email,))
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "VA not found"}), 404
                
                # Check if job is still available
                cur.execute("""
                    SELECT status FROM service_requests WHERE id = %s
                """, (job_id,))
                
                job = cur.fetchone()
                
                if not job:
                    return jsonify({"ok": False, "error": "Job not found"}), 404
                
                if job['status'] != 'pending':
                    return jsonify({"ok": False, "error": "Job already claimed"}), 400
                
                # Claim the job
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'claimed',
                        claimed_by_va_id = %s,
                        claimed_by_va_email = %s,
                        claimed_at = NOW(),
                        timer_started_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s AND status = 'pending'
                """, (va['id'], va_email, job_id))
                
                if cur.rowcount == 0:
                    return jsonify({"ok": False, "error": "Job was just claimed by another VA"}), 400
                
                # Log activity
                cur.execute("""
                    INSERT INTO va_activity_log (va_id, service_request_id, action, notes)
                    VALUES (%s, %s, 'claimed', 'Job claimed')
                """, (va['id'], job_id))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Job claimed successfully"})
    
    except Exception as e:
        print(f"т?O Claim job error: {e}")
        return jsonify({"ok": False, "error": "Failed to claim job"}), 500


@app.post("/api/va/jobs/<int:job_id>/submit")
def api_va_submit_job(job_id):
    """Submit job results"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    data = request.get_json()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    notes = data.get('notes', '').strip()
    hours_used = data.get('hours_used')
    call_outcome = data.get('call_outcome')
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get VA
                cur.execute("SELECT id FROM va_users WHERE email = %s AND active = TRUE", (va_email,))
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "VA not found"}), 404
                
                # Check if this VA owns this job
                cur.execute("""
                    SELECT status, service_type, timer_started_at FROM service_requests 
                    WHERE id = %s AND claimed_by_va_id = %s
                """, (job_id, va['id']))
                
                job = cur.fetchone()
                
                if not job:
                    return jsonify({"ok": False, "error": "Job not found or not yours"}), 404
                
                if job['status'] not in ['claimed', 'in_progress']:
                    return jsonify({"ok": False, "error": "Job cannot be submitted in current status"}), 400
                
                # Calculate total time worked
                total_seconds = 0
                if job['timer_started_at']:
                    from datetime import datetime, timezone
                    elapsed = datetime.now(timezone.utc) - job['timer_started_at'].replace(tzinfo=timezone.utc)
                    total_seconds = int(elapsed.total_seconds())
                
                # Update job with results
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'submitted',
                        phone = %s,
                        email = %s,
                        notes = %s,
                        hours_used = %s,
                        call_outcome = %s,
                        total_time_seconds = %s,
                        submitted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                """, (phone or None, email or None, notes or None, hours_used, call_outcome, total_seconds, job_id))
                
                # Log activity
                cur.execute("""
                    INSERT INTO va_activity_log (va_id, service_request_id, action, notes)
                    VALUES (%s, %s, 'submitted', %s)
                """, (va['id'], job_id, 'Results submitted for review'))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Results submitted for review"})
    
    except Exception as e:
        print(f"т?O Submit job error: {e}")
        return jsonify({"ok": False, "error": "Failed to submit results"}), 500


@app.post("/api/va/jobs/<int:job_id>/cancel")
def api_va_cancel_job(job_id):
    """Cancel a claimed job and return it to queue"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get VA
                cur.execute("SELECT id FROM va_users WHERE email = %s AND active = TRUE", (va_email,))
                va = cur.fetchone()
                
                if not va:
                    return jsonify({"ok": False, "error": "VA not found"}), 404
                
                # Return job to queue
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'pending',
                        claimed_by_va_id = NULL,
                        claimed_by_va_email = NULL,
                        claimed_at = NULL,
                        updated_at = NOW()
                    WHERE id = %s AND claimed_by_va_id = %s AND status IN ('claimed', 'in_progress')
                """, (job_id, va['id']))
                
                if cur.rowcount == 0:
                    return jsonify({"ok": False, "error": "Job not found or cannot be cancelled"}), 400
                
                # Log activity
                cur.execute("""
                    INSERT INTO va_activity_log (va_id, service_request_id, action, notes)
                    VALUES (%s, %s, 'cancelled', 'Job cancelled and returned to queue')
                """, (va['id'], job_id))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Job cancelled and returned to queue"})
    
    except Exception as e:
        print(f"т?O Cancel job error: {e}")
        return jsonify({"ok": False, "error": "Failed to cancel job"}), 500
# DUAL MODEL API - ADD TO app_saas_automated.py
# JV Partners + End Buyers System

from datetime import datetime, date

# ADMIN ENDPOINTS - Listing Management

@app.post("/api/admin/listings/create")
def admin_create_listing():
    """Admin creates new property listing"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO property_listings (
                        property_id, list_type, visibility, title, description,
                        purchase_price, repair_estimate, arv,
                        jv_enabled, jv_split_percentage, jv_terms,
                        direct_sale_enabled, direct_sale_price, platform_profit_percentage,
                        earnest_deposit_required, status, created_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id
                """, (
                    data.get('property_id'),
                    data.get('list_type', 'jv_only'),
                    data.get('visibility', 'jv_only'),
                    data.get('title'),
                    data.get('description'),
                    data.get('purchase_price'),
                    data.get('repair_estimate', 0),
                    data.get('arv'),
                    data.get('jv_enabled', True),
                    data.get('jv_split_percentage', 40.00),
                    data.get('jv_terms'),
                    data.get('direct_sale_enabled', False),
                    data.get('direct_sale_price'),
                    data.get('platform_profit_percentage', 80.00),
                    data.get('earnest_deposit_required', 1000.00),
                    'active',
                    u['email']
                ))
                
                listing_id = cur.fetchone()['id']
                conn.commit()
                
                return jsonify({"ok": True, "listing_id": listing_id})
                
    except Exception as e:
        print(f"т?O Create listing error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/admin/listings")
def admin_get_listings():
    """Admin views all listings"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    status_filter = request.args.get('status', 'active')
    list_type = request.args.get('list_type')
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                query = """
                    SELECT 
                        pl.*,
                        p.address, p.parcel, p.owner,
                        COUNT(DISTINCT jc.id) FILTER (WHERE jc.status = 'active') as active_jv_claims,
                        COUNT(DISTINCT dp.id) FILTER (WHERE dp.contract_status NOT IN ('cancelled', 'closed')) as active_purchases
                    FROM property_listings pl
                    LEFT JOIN properties p ON pl.property_id = p.id
                    LEFT JOIN jv_claims jc ON pl.id = jc.listing_id
                    LEFT JOIN direct_purchases dp ON pl.id = dp.listing_id
                    WHERE pl.status = %s
                """
                params = [status_filter]
                
                if list_type:
                    query += " AND pl.list_type = %s"
                    params.append(list_type)
                
                query += " GROUP BY pl.id, p.address, p.parcel, p.owner ORDER BY pl.created_at DESC"
                
                cur.execute(query, params)
                listings = cur.fetchall()
                
        return jsonify({"ok": True, "listings": listings})
        
    except Exception as e:
        print(f"т?O Get listings error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.put("/api/admin/listings/<int:listing_id>")
def admin_update_listing(listing_id):
    """Admin updates listing"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                # Build dynamic update query
                fields = []
                values = []
                
                updatable_fields = [
                    'title', 'description', 'list_type', 'visibility',
                    'purchase_price', 'repair_estimate', 'arv',
                    'jv_enabled', 'jv_split_percentage', 'jv_terms',
                    'direct_sale_enabled', 'direct_sale_price', 
                    'platform_profit_percentage', 'status'
                ]
                
                for field in updatable_fields:
                    if field in data:
                        fields.append(f"{field} = %s")
                        values.append(data[field])
                
                if not fields:
                    return jsonify({"ok": False, "error": "No fields to update"}), 400
                
                values.append(listing_id)
                query = f"UPDATE property_listings SET {', '.join(fields)} WHERE id = %s"
                
                cur.execute(query, values)
                conn.commit()
                
                return jsonify({"ok": True})
                
    except Exception as e:
        print(f"т?O Update listing error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/admin/listings/<int:listing_id>/claims")
def admin_get_listing_claims(listing_id):
    """Admin views JV claims for a listing"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM jv_claims
                    WHERE listing_id = %s
                    ORDER BY claimed_at DESC
                """, (listing_id,))
                
                claims = cur.fetchall()
                
        return jsonify({"ok": True, "claims": claims})
        
    except Exception as e:
        print(f"т?O Get claims error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/admin/jv-claims/<int:claim_id>/approve-buyer")
def admin_approve_buyer(claim_id):
    """Admin approves JV partner's buyer"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jv_claims
                    SET status = 'buyer_approved',
                        buyer_approved_at = NOW(),
                        buyer_approved_by = %s,
                        admin_notes = %s
                    WHERE id = %s
                """, (u['email'], data.get('admin_notes'), claim_id))
                
                conn.commit()
                
        return jsonify({"ok": True})
        
    except Exception as e:
        print(f"т?O Approve buyer error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/admin/jv-claims/<int:claim_id>/close-deal")
def admin_close_jv_deal(claim_id):
    """Admin marks JV deal as closed and calculates earnings"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    final_sale_price = float(data.get('final_sale_price'))
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get claim and listing details
                cur.execute("""
                    SELECT jc.*, pl.total_investment, pl.jv_split_percentage
                    FROM jv_claims jc
                    JOIN property_listings pl ON jc.listing_id = pl.id
                    WHERE jc.id = %s
                """, (claim_id,))
                
                claim = cur.fetchone()
                
                # Calculate earnings
                gross_profit = final_sale_price - float(claim['total_investment'])
                partner_percentage = float(claim['jv_split_percentage'])
                partner_earned = gross_profit * (partner_percentage / 100)
                platform_earned = gross_profit - partner_earned
                
                # Update claim
                cur.execute("""
                    UPDATE jv_claims
                    SET status = 'closed',
                        final_sale_price = %s,
                        gross_profit = %s,
                        partner_earned = %s,
                        platform_earned = %s,
                        actual_close_date = %s,
                        payment_status = 'approved'
                    WHERE id = %s
                """, (final_sale_price, gross_profit, partner_earned, platform_earned, 
                      date.today(), claim_id))
                
                # Update listing status
                cur.execute("""
                    UPDATE property_listings
                    SET status = 'closed', closed_at = NOW()
                    WHERE id = %s
                """, (claim['listing_id'],))
                
                conn.commit()
                
                return jsonify({
                    "ok": True,
                    "gross_profit": float(gross_profit),
                    "partner_earned": float(partner_earned),
                    "platform_earned": float(platform_earned)
                })
                
    except Exception as e:
        print(f"т?O Close deal error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# JV PARTNER ENDPOINTS

@app.get("/api/jv/listings")
def jv_get_available_listings():
    """JV partners browse available opportunities"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM active_jv_listings
                    ORDER BY created_at DESC
                    LIMIT 50
                """)
                
                listings = cur.fetchall()
                
        return jsonify({"ok": True, "listings": listings})
        
    except Exception as e:
        print(f"т?O Get JV listings error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/jv/claims/create")
def jv_claim_deal():
    """JV partner claims a deal"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    listing_id = data.get('listing_id')
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Check if already claimed by this user
                cur.execute("""
                    SELECT id FROM jv_claims
                    WHERE listing_id = %s AND user_email = %s AND status = 'active'
                """, (listing_id, u['email']))
                
                existing = cur.fetchone()
                if existing:
                    return jsonify({"ok": False, "error": "You already claimed this deal"}), 400
                
                # Get JV percentage from listing
                cur.execute("""
                    SELECT jv_split_percentage FROM property_listings WHERE id = %s
                """, (listing_id,))
                
                listing = cur.fetchone()
                
                # Create claim
                cur.execute("""
                    INSERT INTO jv_claims (listing_id, user_email, jv_percentage, status)
                    VALUES (%s, %s, %s, 'active')
                    RETURNING id
                """, (listing_id, u['email'], listing['jv_split_percentage']))
                
                claim_id = cur.fetchone()['id']
                conn.commit()
                
                return jsonify({"ok": True, "claim_id": claim_id})
                
    except Exception as e:
        print(f"т?O Claim deal error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/jv/my-claims")
def jv_get_my_claims():
    """JV partner views their active deals"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        jc.*,
                        pl.title, pl.purchase_price, pl.arv,
                        p.address, p.parcel
                    FROM jv_claims jc
                    JOIN property_listings pl ON jc.listing_id = pl.id
                    JOIN properties p ON pl.property_id = p.id
                    WHERE jc.user_email = %s
                    ORDER BY jc.claimed_at DESC
                """, (u['email'],))
                
                claims = cur.fetchall()
                
        return jsonify({"ok": True, "claims": claims})
        
    except Exception as e:
        print(f"т?O Get my claims error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.put("/api/jv/claims/<int:claim_id>/submit-buyer")
def jv_submit_buyer(claim_id):
    """JV partner submits buyer information"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jv_claims
                    SET buyer_name = %s,
                        buyer_email = %s,
                        buyer_phone = %s,
                        buyer_notes = %s,
                        buyer_submitted_at = NOW(),
                        status = 'buyer_submitted'
                    WHERE id = %s AND user_email = %s
                """, (
                    data.get('buyer_name'),
                    data.get('buyer_email'),
                    data.get('buyer_phone'),
                    data.get('buyer_notes'),
                    claim_id,
                    u['email']
                ))
                
                conn.commit()
                
        return jsonify({"ok": True})
        
    except Exception as e:
        print(f"т?O Submit buyer error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/jv/earnings")
def jv_get_earnings():
    """JV partner views earnings dashboard"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM jv_partner_earnings
                    WHERE user_email = %s
                """, (u['email'],))
                
                earnings = cur.fetchone()
                
                if not earnings:
                    earnings = {
                        "total_claims": 0,
                        "active_claims": 0,
                        "closed_deals": 0,
                        "total_paid": 0,
                        "pending_payment": 0,
                        "avg_deal_earnings": 0
                    }
                
        return jsonify({"ok": True, "earnings": earnings})
        
    except Exception as e:
        print(f"т?O Get earnings error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# END BUYER ENDPOINTS

@app.get("/api/marketplace/listings")
def marketplace_get_listings():
    """End buyers browse properties for sale"""
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        pl.*,
                        p.address, p.parcel, p.zip_code, p.owner
                    FROM active_direct_listings pl
                    JOIN properties p ON pl.property_id = p.id
                    ORDER BY pl.created_at DESC
                    LIMIT 50
                """)
                
                listings = cur.fetchall()
                
        return jsonify({"ok": True, "listings": listings})
        
    except Exception as e:
        print(f"т?O Get marketplace listings error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/marketplace/listings/<int:listing_id>")
def marketplace_get_listing_details(listing_id):
    """Get detailed property information"""
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        pl.*,
                        p.address, p.parcel, p.zip_code, p.owner, p.current_total_due
                    FROM property_listings pl
                    JOIN properties p ON pl.property_id = p.id
                    WHERE pl.id = %s
                """, (listing_id,))
                
                listing = cur.fetchone()
                
                if not listing:
                    return jsonify({"ok": False, "error": "Listing not found"}), 404
                
                # Track view
                user_email = None
                user = require_login(admin=False)
                if user:
                    user_email = user['email']
                
                cur.execute("""
                    INSERT INTO listing_views (listing_id, user_email, user_type)
                    VALUES (%s, %s, 'end_buyer')
                """, (listing_id, user_email))
                
                conn.commit()
                
        return jsonify({"ok": True, "listing": listing})
        
    except Exception as e:
        print(f"т?O Get listing details error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/marketplace/purchase")
def marketplace_purchase_property():
    """End buyer initiates purchase"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get listing details
                cur.execute("""
                    SELECT * FROM property_listings WHERE id = %s
                """, (data.get('listing_id'),))
                
                listing = cur.fetchone()
                
                if not listing or not listing['direct_sale_enabled']:
                    return jsonify({"ok": False, "error": "Property not available for purchase"}), 400
                
                # Calculate platform fee
                offer_price = float(data.get('offer_price', listing['direct_sale_price']))
                gross_profit = offer_price - float(listing['total_investment'])
                platform_percentage = float(listing['platform_profit_percentage'])
                platform_fee = gross_profit * (platform_percentage / 100)
                
                # Create purchase record
                cur.execute("""
                    INSERT INTO direct_purchases (
                        listing_id, buyer_email, buyer_name, buyer_phone,
                        offer_price, earnest_deposit, financing_type,
                        gross_profit, platform_fee, platform_percentage,
                        contract_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'offer_submitted')
                    RETURNING id
                """, (
                    data.get('listing_id'),
                    u['email'],
                    data.get('buyer_name'),
                    data.get('buyer_phone'),
                    offer_price,
                    data.get('earnest_deposit'),
                    data.get('financing_type'),
                    gross_profit,
                    platform_fee,
                    platform_percentage
                ))
                
                purchase_id = cur.fetchone()['id']
                conn.commit()
                
                return jsonify({
                    "ok": True,
                    "purchase_id": purchase_id,
                    "earnest_deposit": float(data.get('earnest_deposit')),
                    "platform_fee": float(platform_fee)
                })
                
    except Exception as e:
        print(f"т?O Purchase error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/buyer/my-purchases")
def buyer_get_my_purchases():
    """End buyer views their purchases"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        dp.*,
                        pl.title, pl.purchase_price,
                        p.address, p.parcel
                    FROM direct_purchases dp
                    JOIN property_listings pl ON dp.listing_id = pl.id
                    JOIN properties p ON pl.property_id = p.id
                    WHERE dp.buyer_email = %s
                    ORDER BY dp.offer_submitted_at DESC
                """, (u['email'],))
                
                purchases = cur.fetchall()
                
        return jsonify({"ok": True, "purchases": purchases})
        
    except Exception as e:
        print(f"т?O Get purchases error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ANALYTICS & STATS

@app.get("/api/admin/revenue-summary")
def admin_revenue_summary():
    """Admin views revenue summary"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM platform_revenue_summary")
                revenue = cur.fetchall()
                
        return jsonify({"ok": True, "revenue": revenue})
        
    except Exception as e:
        print(f"т?O Revenue summary error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# END OF DUAL MODEL API


# ========== END VA PORTAL API ==========



# ========== HYBRID REWARDS SYSTEM API ==========

def update_user_rewards_tracking(user_email, amount_spent=0, time_seconds=0):
    """Update user's spending and time tracking, check for tier upgrades"""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_spending_tracker WHERE user_email = %s", (user_email,))
                user_data = cur.fetchone()
                
                if not user_data:
                    cur.execute("""
                        INSERT INTO user_spending_tracker 
                            (user_email, total_spent, current_tier_spent, total_time_seconds, tier_level, time_bonus_tier)
                        VALUES (%s, %s, %s, %s, 0, 0)
                        RETURNING *
                    """, (user_email, amount_spent, amount_spent, time_seconds))
                    user_data = cur.fetchone()
                else:
                    new_total_spent = float(user_data['total_spent']) + amount_spent
                    new_current_tier_spent = float(user_data['current_tier_spent']) + amount_spent
                    new_total_time = int(user_data['total_time_seconds']) + time_seconds
                    
                    cur.execute("""
                        UPDATE user_spending_tracker
                        SET total_spent = %s,
                            current_tier_spent = %s,
                            total_time_seconds = %s,
                            last_purchase_at = CASE WHEN %s > 0 THEN NOW() ELSE last_purchase_at END,
                            updated_at = NOW()
                        WHERE user_email = %s
                        RETURNING *
                    """, (new_total_spent, new_current_tier_spent, new_total_time, amount_spent, user_email))
                    user_data = cur.fetchone()
                
                total_minutes = int(user_data['total_time_seconds']) / 60
                cur.execute("""
                    SELECT MAX(tier_level) as max_tier
                    FROM time_bonus_tiers
                    WHERE minutes_required <= %s AND is_active = TRUE
                """, (total_minutes,))
                
                result = cur.fetchone()
                new_time_tier = result['max_tier'] if result and result['max_tier'] is not None else 0
                
                if new_time_tier > user_data['time_bonus_tier']:
                    cur.execute("""
                        UPDATE user_spending_tracker
                        SET time_bonus_tier = %s
                        WHERE user_email = %s
                    """, (new_time_tier, user_email))
                
                conn.commit()
                return True
    except Exception as e:
        print(f"т?O Update rewards tracking error: {e}")
        return False


@app.post("/api/user/update-time")
def update_user_time():
    """Update user's active time"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json(silent=True) or {}
    time_seconds = int(data.get('time_seconds', 0))
    
    if time_seconds <= 0:
        return jsonify({"ok": False, "error": "Invalid time"}), 400
    
    success = update_user_rewards_tracking(u['email'], amount_spent=0, time_seconds=time_seconds)
    
    if success:
        return jsonify({"ok": True})
    else:
        return jsonify({"ok": False, "error": "Failed to update time"}), 500


@app.get("/api/user/rewards-status")
def get_user_rewards_status():
    """Get complete user reward status"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_complete_reward_status WHERE user_email = %s", (u['email'],))
                status = cur.fetchone()
                
                if not status:
                    return jsonify({
                        "ok": True, "total_spent": 0, "current_tier_spent": 0,
                        "spending_tier": 0, "spending_tier_name": "Bronze",
                        "base_reward_amount": 5.00, "total_minutes": 0,
                        "time_bonus_tier": 0, "time_bonus_name": "Explorer",
                        "time_multiplier": 1.0, "time_badge": "dY"?",
                        "boosted_reward_amount": 5.00, "can_claim_reward": False,
                        "next_spending_tier": 100.00, "next_time_minutes": 180,
                        "lifetime_points": 0
                    })
                
                cur.execute("SELECT spending_required FROM reward_tiers WHERE tier_level = %s AND is_active = TRUE", (status['spending_tier'],))
                current_tier = cur.fetchone()
                spending_required = float(current_tier['spending_required']) if current_tier else 100
                can_claim = float(status['current_tier_spent']) >= spending_required
                
                return jsonify({
                    "ok": True,
                    "total_spent": float(status['total_spent']),
                    "current_tier_spent": float(status['current_tier_spent']),
                    "spending_tier": int(status['spending_tier']),
                    "spending_tier_name": status['spending_tier_name'],
                    "base_reward_amount": float(status['base_reward_amount']) if status['base_reward_amount'] else 5.00,
                    "total_minutes": int(status['total_minutes']),
                    "time_bonus_tier": int(status['time_bonus_tier']),
                    "time_bonus_name": status['time_bonus_name'],
                    "time_multiplier": float(status['time_multiplier']),
                    "time_badge": status['time_badge'],
                    "boosted_reward_amount": float(status['boosted_reward_amount']) if status['boosted_reward_amount'] else 5.00,
                    "can_claim_reward": can_claim,
                    "spending_required": spending_required,
                    "amount_until_reward": max(0, spending_required - float(status['current_tier_spent'])),
                    "next_time_minutes": int(status['next_time_minutes']) if status['next_time_minutes'] else 180,
                    "next_time_multiplier": float(status['next_time_multiplier']) if status['next_time_multiplier'] else 1.1,
                    "lifetime_points": int(status['lifetime_points'])
                })
    except Exception as e:
        print(f"т?O Get rewards status error: {e}")
        return jsonify({"ok": False, "error": "Failed to load rewards status"}), 500


@app.post("/api/user/claim-reward")
def claim_spending_reward():
    """User claims their spending tier reward"""
    u = require_login()
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_complete_reward_status WHERE user_email = %s", (u['email'],))
                status = cur.fetchone()
                
                if not status:
                    return jsonify({"ok": False, "error": "No reward data found"}), 404
                
                cur.execute("SELECT * FROM reward_tiers WHERE tier_level = %s AND is_active = TRUE", (status['spending_tier'],))
                tier = cur.fetchone()
                
                if not tier:
                    return jsonify({"ok": False, "error": "Tier not found"}), 404
                
                if float(status['current_tier_spent']) < float(tier['spending_required']):
                    return jsonify({
                        "ok": False,
                        "error": f"Need ${tier['spending_required'] - float(status['current_tier_spent']):.2f} more to claim"
                    }), 400
                
                base_reward = float(tier['reward_amount'])
                time_multiplier = float(status['time_multiplier'])
                final_reward = round(base_reward * time_multiplier, 2)
                
                cur.execute("""
                    INSERT INTO reward_claims
                        (user_email, claim_type, tier_level, total_spent_at_claim,
                         points_at_claim, reward_amount, status)
                    VALUES (%s, 'tier_reward', %s, %s, %s, %s, 'approved')
                    RETURNING id
                """, (u['email'], status['spending_tier'], status['total_spent'],
                      status['lifetime_points'], final_reward))
                
                claim_id = cur.fetchone()['id']
                
                cur.execute("""
                    UPDATE user_spending_tracker
                    SET tier_level = tier_level + 1,
                        current_tier_spent = 0,
                        rewards_claimed_count = rewards_claimed_count + 1,
                        updated_at = NOW()
                    WHERE user_email = %s
                """, (u['email'],))
                
                conn.commit()
                
                return jsonify({
                    "ok": True, "claim_id": claim_id, "reward_amount": final_reward,
                    "base_reward": base_reward, "time_multiplier": time_multiplier,
                    "tier_name": tier['tier_name'],
                    "message": f"Claimed ${final_reward} {tier['tier_name']}!"
                })
    except Exception as e:
        print(f"т?O Claim reward error: {e}")
        return jsonify({"ok": False, "error": "Failed to claim reward"}), 500

# ========== END REWARDS SYSTEM API ==========

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)


# Property Notes API
@app.get("/api/properties/<int:property_id>/notes")
def get_property_notes(property_id):
    """Get all notes for a property"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, note, user_email, created_at, updated_at
                    FROM property_notes
                    WHERE property_id = %s
                    ORDER BY created_at DESC
                """, (property_id,))
                
                notes = cur.fetchall()
                
                return jsonify({
                    "ok": True,
                    "notes": notes
                })
    
    except Exception as e:
        print(f"т?O Get notes error: {e}")
        return jsonify({"ok": False, "error": "Failed to load notes"}), 500


@app.post("/api/properties/<int:property_id>/notes")
def add_property_note(property_id):
    """Add a note to a property"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json()
    note_text = data.get('note', '').strip()
    
    if not note_text:
        return jsonify({"ok": False, "error": "Note cannot be empty"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get property parcel
                cur.execute("SELECT parcel FROM properties WHERE id = %s", (property_id,))
                prop = cur.fetchone()
                
                if not prop:
                    return jsonify({"ok": False, "error": "Property not found"}), 404
                
                # Add note
                cur.execute("""
                    INSERT INTO property_notes (property_id, parcel, user_email, note)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, created_at
                """, (property_id, prop['parcel'], u['email'], note_text))
                
                result = cur.fetchone()
                conn.commit()
                
                return jsonify({
                    "ok": True,
                    "note_id": result['id'],
                    "created_at": result['created_at'].isoformat()
                })
    
    except Exception as e:
        print(f"т?O Add note error: {e}")
        return jsonify({"ok": False, "error": "Failed to add note"}), 500


@app.delete("/api/properties/<int:property_id>/notes/<int:note_id>")
def delete_property_note(property_id, note_id):
    """Delete a note (admin or note creator only)"""
    u = require_login(admin=False)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Check if user owns this note or is admin
                cur.execute("""
                    SELECT user_email FROM property_notes
                    WHERE id = %s AND property_id = %s
                """, (note_id, property_id))
                
                note = cur.fetchone()
                
                if not note:
                    return jsonify({"ok": False, "error": "Note not found"}), 404
                
                if note['user_email'] != u['email'] and not u.get('is_admin'):
                    return jsonify({"ok": False, "error": "Not authorized to delete this note"}), 403
                
                # Delete note
                cur.execute("DELETE FROM property_notes WHERE id = %s", (note_id,))
                conn.commit()
                
                return jsonify({"ok": True, "message": "Note deleted"})
    
    except Exception as e:
        print(f"т?O Delete note error: {e}")
        return jsonify({"ok": False, "error": "Failed to delete note"}), 500


# File Upload API
@app.post("/api/va/jobs/<int:job_id>/upload")
def upload_job_file(job_id):
    """Upload proof of work file for a job"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"ok": False, "error": "No file selected"}), 400
    
    # Check file size (50MB limit)
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Seek back to start
    
    if file_size > 50 * 1024 * 1024:  # 50MB
        return jsonify({"ok": False, "error": "File too large (max 50MB)"}), 400
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Verify VA owns this job
                cur.execute("""
                    SELECT id FROM service_requests 
                    WHERE id = %s AND claimed_by_va_email = %s
                    AND status IN ('claimed', 'in_progress')
                """, (job_id, va_email))
                
                job = cur.fetchone()
                
                if not job:
                    return jsonify({"ok": False, "error": "Job not found or not yours"}), 404
                
                # Check total file size for this request
                cur.execute("""
                    SELECT COALESCE(SUM(file_size), 0) as total_size
                    FROM service_request_files
                    WHERE service_request_id = %s
                """, (job_id,))
                
                result = cur.fetchone()
                current_total = result['total_size']
                
                if current_total + file_size > 200 * 1024 * 1024:  # 200MB total
                    return jsonify({"ok": False, "error": "Total file size limit exceeded (max 200MB)"}), 400
                
                # Generate unique filename
                import os
                import uuid
                from werkzeug.utils import secure_filename
                
                original_filename = secure_filename(file.filename)
                file_ext = os.path.splitext(original_filename)[1]
                unique_filename = f"{uuid.uuid4().hex}{file_ext}"
                
                # Save to /mnt/user-data/outputs/uploads (temporary - should use S3/Supabase Storage in production)
                upload_dir = "/mnt/user-data/outputs/uploads"
                os.makedirs(upload_dir, exist_ok=True)
                file_path = os.path.join(upload_dir, unique_filename)
                file.save(file_path)
                
                # For now, just store filename. In production, upload to S3/Supabase Storage
                file_url = f"/uploads/{unique_filename}"
                
                # Store file record
                cur.execute("""
                    INSERT INTO service_request_files (
                        service_request_id,
                        filename,
                        original_filename,
                        file_size,
                        file_type,
                        file_url,
                        uploaded_by_va_email
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    job_id,
                    unique_filename,
                    original_filename,
                    file_size,
                    file.content_type or 'application/octet-stream',
                    file_url,
                    va_email
                ))
                
                file_record = cur.fetchone()
                conn.commit()
                
                return jsonify({
                    "ok": True,
                    "file_id": file_record['id'],
                    "filename": original_filename,
                    "size": file_size
                })
    
    except Exception as e:
        print(f"т?O File upload error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": "Failed to upload file"}), 500


@app.get("/api/va/jobs/<int:job_id>/files")
def get_job_files(job_id):
    """Get all uploaded files for a job"""
    # Can be accessed by VA who owns job or admin
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, original_filename, file_size, file_type, 
                           file_url, uploaded_at
                    FROM service_request_files
                    WHERE service_request_id = %s
                    ORDER BY uploaded_at DESC
                """, (job_id,))
                
                files = cur.fetchall()
                
                return jsonify({
                    "ok": True,
                    "files": files
                })
    
    except Exception as e:
        print(f"т?O Get files error: {e}")
        return jsonify({"ok": False, "error": "Failed to load files"}), 500


@app.delete("/api/va/jobs/<int:job_id>/files/<int:file_id>")
def delete_job_file(job_id, file_id):
    """Delete an uploaded file"""
    va_email = request.headers.get('X-VA-Email', '').strip().lower()
    
    if not va_email:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Verify ownership
                cur.execute("""
                    SELECT f.filename, f.file_url
                    FROM service_request_files f
                    JOIN service_requests sr ON sr.id = f.service_request_id
                    WHERE f.id = %s AND f.service_request_id = %s
                    AND sr.claimed_by_va_email = %s
                """, (file_id, job_id, va_email))
                
                file_record = cur.fetchone()
                
                if not file_record:
                    return jsonify({"ok": False, "error": "File not found"}), 404
                
                # Delete file from disk
                import os
                file_path = os.path.join("/mnt/user-data/outputs/uploads", file_record['filename'])
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                # Delete from database
                cur.execute("DELETE FROM service_request_files WHERE id = %s", (file_id,))
                conn.commit()
                
                return jsonify({"ok": True, "message": "File deleted"})
    
    except Exception as e:
        print(f"т?O Delete file error: {e}")
        return jsonify({"ok": False, "error": "Failed to delete file"}), 500

