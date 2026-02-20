"""
Flask App with Automated Tax Lead Scraping - SaaS Ready

Features:
1. Automated scraping via cron/scheduler
2. API endpoints to trigger scrapes
3. View scrape jobs and results
4. Admin controls
5. Multi-county support (future-proof)
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
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
                print(f"✅ Database connection successful on attempt {attempt + 1}")
            return conn
        except psycopg2.OperationalError as e:
            if attempt < 2:  # Not the last attempt
                wait_time = (attempt + 1) * 3  # 3s, 6s
                print(f"⚠️ Database connection failed (attempt {attempt + 1}/3), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ Database connection failed after 3 attempts")
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
    
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = max(1, min(500, int(request.args.get("page_size", "50"))))
    except:
        page = 1
        page_size = 50
    
    # Build query
    where_clauses = []
    params = []
    
    if county:
        where_clauses.append("county = %s")
        params.append(county)
    
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
    u = require_login()
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
        print(f"⚠️ Classification error: {e}, issued_date={issued_date_str}")
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
    print(f"📦 Grouped into {len(parcel_groups)} unique parcels (from {len(rows)} rows)")
    
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
                    error_msg = f"❌ ERROR for parcel {parcel}: {str(e)}"
                    print(error_msg)
                    errors.append(error_msg)
                    if len(errors) > 20:
                        print(f"⚠️ Too many errors, stopping at 20. Total errors so far: {len(errors)}")
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
            print(f"📊 Starting Excel upload for {county}...")
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
            print(f"✅ Upload complete: {imported} imported, {skipped} skipped")
        
        else:
            # CSV/TSV file processing
            print(f"📊 Starting CSV upload for {county}...")
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
            
            print(f"✅ Upload complete: {imported} imported, {skipped} skipped")
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "skipped_filtered": result.get('skipped_filtered', 0) if 'result' in locals() else 0,
            "errors": errors[:10]
        })
    
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Initialize database tables on startup
try:
    db_init()
    print("✅ Database initialized successfully!")
except Exception as e:
    print(f"⚠️  Database init failed: {e}")


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
        print(f"❌ Get pricing error: {e}")
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
        print(f"❌ Update pricing error: {e}")
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
        print(f"❌ Toggle pricing error: {e}")
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
        print(f"❌ Admin service requests error: {e}")
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
        print(f"❌ Approve request error: {e}")
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
        print(f"❌ Reject request error: {e}")
        return jsonify({"ok": False, "error": "Failed to reject request"}), 500


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
                conn.commit()
                
                # Return VA data (without password hash)
                va_data = dict(va)
                del va_data['password_hash']
                
                # Convert Decimal/string to float for JSON
                va_data['total_earned'] = float(va_data.get('total_earned') or 0)
                
                return jsonify({"ok": True, "va": va_data})
    
    except Exception as e:
        print(f"❌ VA login error: {e}")
        return jsonify({"ok": False, "error": "Login failed"}), 500


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
        print(f"❌ VA jobs error: {e}")
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
        print(f"❌ Claim job error: {e}")
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
                    SELECT status, service_type FROM service_requests 
                    WHERE id = %s AND claimed_by_va_id = %s
                """, (job_id, va['id']))
                
                job = cur.fetchone()
                
                if not job:
                    return jsonify({"ok": False, "error": "Job not found or not yours"}), 404
                
                if job['status'] not in ['claimed', 'in_progress']:
                    return jsonify({"ok": False, "error": "Job cannot be submitted in current status"}), 400
                
                # Update job with results
                cur.execute("""
                    UPDATE service_requests
                    SET status = 'submitted',
                        phone = %s,
                        email = %s,
                        notes = %s,
                        hours_used = %s,
                        call_outcome = %s,
                        submitted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                """, (phone or None, email or None, notes or None, hours_used, call_outcome, job_id))
                
                # Log activity
                cur.execute("""
                    INSERT INTO va_activity_log (va_id, service_request_id, action, notes)
                    VALUES (%s, %s, 'submitted', %s)
                """, (va['id'], job_id, 'Results submitted for review'))
                
                conn.commit()
                
                return jsonify({"ok": True, "message": "Results submitted for review"})
    
    except Exception as e:
        print(f"❌ Submit job error: {e}")
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
        print(f"❌ Cancel job error: {e}")
        return jsonify({"ok": False, "error": "Failed to cancel job"}), 500


# ========== END VA PORTAL API ==========


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
