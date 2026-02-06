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


@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "db_url_set": bool(DATABASE_URL),
        "server_time": datetime.now(timezone.utc).isoformat() + "Z"
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
        if issued_date_str and '/' in issued_date_str:
            issued_date = datetime.strptime(issued_date_str, '%m/%d/%Y')
            years_old = (datetime.now() - issued_date).days / 365.25
        else:
            years_old = 0
        
        if cert_status and 'redeemed' in str(cert_status).lower():
            return 'current'
        
        if deed_status and str(deed_status).strip():
            return 'tax_deed_filed'
        
        if years_old >= 2 and years_old <= 5:
            return 'sweet_spot'
        elif years_old > 5:
            return 'danger_zone'
        elif years_old >= 1:
            return 'pre_lien'
        else:
            return 'current'
    except:
        return 'pre_lien'


def process_excel_batch(rows, county):
    """Process batch of Excel rows with correct county-taxes.net column mapping"""
    imported = 0
    errors = []
    
    with db_conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                try:
                    parcel = str(row.get('parcel', '')).strip()
                    if not parcel or parcel == 'None':
                        continue
                    
                    # Use exact column names from county-taxes.net
                    owner = str(row.get('Owner Name', '')).strip()
                    address = str(row.get('Property Address', '')).strip()
                    cert_number = str(row.get('Cert #', '')).strip()
                    issued_date = str(row.get('Issued Date', '')).strip()
                    cert_status = str(row.get('Cert Status', '')).strip()
                    deed_status = str(row.get('Deed Status', '')).strip()
                    
                    # Parse Face Amount
                    try:
                        face_str = str(row.get('Face Amount', '')).replace('$', '').replace(',', '').strip()
                        face_amount = float(face_str) if face_str and face_str != 'None' else None
                    except:
                        face_amount = None
                    
                    # Classify stage based on certificate data
                    stage = classify_stage_from_cert(cert_status, issued_date, deed_status)
                    has_ntd = bool(deed_status and deed_status.strip() and deed_status != 'None')
                    
                    # Insert or update
                    cur.execute("""
                        INSERT INTO properties (
                            parcel, county, stage, owner, address,
                            certificate_number, face_amount, current_total_due,
                            has_tax_deed_notice, last_verified_at, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW()
                        )
                        ON CONFLICT (parcel) DO UPDATE SET
                            owner = COALESCE(NULLIF(EXCLUDED.owner, ''), properties.owner),
                            address = COALESCE(NULLIF(EXCLUDED.address, ''), properties.address),
                            certificate_number = COALESCE(NULLIF(EXCLUDED.certificate_number, ''), properties.certificate_number),
                            face_amount = COALESCE(EXCLUDED.face_amount, properties.face_amount),
                            current_total_due = COALESCE(EXCLUDED.current_total_due, properties.current_total_due),
                            has_tax_deed_notice = EXCLUDED.has_tax_deed_notice OR properties.has_tax_deed_notice,
                            stage = EXCLUDED.stage,
                            last_verified_at = NOW(),
                            updated_at = NOW()
                    """, (parcel, county, stage, owner, address, cert_number, face_amount, face_amount, has_ntd))
                    
                    imported += 1
                except Exception as e:
                    errors.append(f"Parcel {parcel}: {str(e)}")
                    if len(errors) > 10:
                        break
            
            conn.commit()
    
    return {'imported': imported, 'errors': errors}


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
        BATCH_SIZE = 1000
        imported = 0
        skipped = 0
        errors = []
        
        if is_excel:
            # Excel file processing
            wb = load_workbook(file.stream, read_only=True, data_only=True)
            ws = wb.active
            
            # Get headers
            headers = [str(cell.value).strip() if cell.value else '' for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            
            batch = []
            
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_dict = dict(zip(headers, [str(cell) if cell else '' for cell in row]))
                
                if row_dict.get('parcel'):
                    batch.append(row_dict)
                else:
                    skipped += 1
                
                if len(batch) >= BATCH_SIZE:
                    result = process_excel_batch(batch, county)
                    imported += result['imported']
                    errors.extend(result['errors'])
                    batch = []
            
            if batch:
                result = process_excel_batch(batch, county)
                imported += result['imported']
                errors.extend(result['errors'])
            
            wb.close()
        
        else:
            # CSV/TSV file processing
            file_content = file.read().decode('utf-8', errors='ignore')
            
            # Detect delimiter (tab or comma)
            first_line = file_content.split('\n')[0]
            delimiter = '\t' if '\t' in first_line else ','
            
            csv_reader = csv.DictReader(io.StringIO(file_content), delimiter=delimiter)
            
            batch = []
            
            for row in csv_reader:
                if row.get('parcel'):
                    batch.append(row)
                else:
                    skipped += 1
                
                if len(batch) >= BATCH_SIZE:
                    result = process_excel_batch(batch, county)
                    imported += result['imported']
                    errors.extend(result['errors'])
                    batch = []
            
            if batch:
                result = process_excel_batch(batch, county)
                imported += result['imported']
                errors.extend(result['errors'])
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]
        })
    
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Initialize database on startup (always run, not just when called directly)
db_init()

# Database check on startup
print("\n" + "=" * 70)
print("🔍 DATABASE CHECK ON STARTUP")
print("=" * 70)
try:
    if DATABASE_URL:
        conn = db_conn()
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
        print("✅ Database connection successful!")
    else:
        print("⚠️  DATABASE_URL not set")
except Exception as e:
    print(f"⚠️  Database check failed: {e}")
    import traceback
    traceback.print_exc()
print("=" * 70 + "\n")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
