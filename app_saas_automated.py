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

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, redirect, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import secrets
import pandas as pd

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
    """Upload property data from Excel file"""
    u = require_login(admin=True)
    if not u:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"ok": False, "error": "No file selected"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"ok": False, "error": "Invalid file type. Please upload .xlsx, .xls, or .csv"}), 400
    
    try:
        # Read Excel file
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Column mapping (flexible - handles different column names)
        column_map = {
            # Parcel variations
            'parcel': ['parcel', 'parcel_id', 'parcel_number', 'parcel id', 'account', 'account_no'],
            # Owner variations
            'owner': ['owner', 'owner_name', 'name', 'taxpayer', 'taxpayer_name'],
            # Address variations
            'address': ['address', 'property_address', 'situs_address', 'situs address', 'location'],
            'city': ['city', 'situs_city'],
            'zip': ['zip', 'zip_code', 'zipcode', 'situs_zip'],
            # Financial data
            'current_total_due': ['total_due', 'amount_due', 'total_amount_due', 'amount', 'balance'],
            'face_amount': ['face_amount', 'certificate_amount', 'face amount'],
            # Certificate data
            'certificate_number': ['certificate', 'cert_number', 'certificate_number', 'cert number', 'cert_no'],
            'certificate_year': ['cert_year', 'certificate_year', 'year'],
            # Tax deed notice
            'has_tax_deed_notice': ['tax_deed_notice', 'ntd', 'notice', 'has_notice'],
        }
        
        # Normalize column names
        df.columns = df.columns.str.strip().str.lower()
        
        # Map columns
        mapped_cols = {}
        for target_col, possible_names in column_map.items():
            for name in possible_names:
                if name in df.columns:
                    mapped_cols[target_col] = name
                    break
        
        # Check if we have at least parcel column
        if 'parcel' not in mapped_cols:
            return jsonify({
                "ok": False, 
                "error": f"Could not find parcel column. Available columns: {', '.join(df.columns)}"
            }), 400
        
        imported = 0
        duplicates = 0
        errors = []
        
        with db_conn() as conn:
            with conn.cursor() as cur:
                for idx, row in df.iterrows():
                    try:
                        # Get parcel (required)
                        parcel = str(row[mapped_cols['parcel']]).strip()
                        if not parcel or parcel == 'nan':
                            continue
                        
                        # Get other fields (optional)
                        owner = str(row[mapped_cols.get('owner', mapped_cols['parcel'])]).strip() if 'owner' in mapped_cols else ''
                        address = str(row[mapped_cols.get('address', mapped_cols['parcel'])]).strip() if 'address' in mapped_cols else ''
                        city = str(row[mapped_cols.get('city', mapped_cols['parcel'])]).strip() if 'city' in mapped_cols else ''
                        zip_code = str(row[mapped_cols.get('zip', mapped_cols['parcel'])]).strip() if 'zip' in mapped_cols else ''
                        
                        # Financial data
                        try:
                            total_due = float(row[mapped_cols.get('current_total_due', mapped_cols['parcel'])]) if 'current_total_due' in mapped_cols else None
                        except:
                            total_due = None
                        
                        try:
                            face_amount = float(row[mapped_cols.get('face_amount', mapped_cols['parcel'])]) if 'face_amount' in mapped_cols else None
                        except:
                            face_amount = None
                        
                        # Certificate data
                        cert_number = str(row[mapped_cols.get('certificate_number', mapped_cols['parcel'])]).strip() if 'certificate_number' in mapped_cols else ''
                        
                        # Tax deed notice
                        has_ntd = False
                        if 'has_tax_deed_notice' in mapped_cols:
                            ntd_val = str(row[mapped_cols['has_tax_deed_notice']]).lower()
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
                        
                        if cur.rowcount > 0:
                            imported += 1
                        else:
                            duplicates += 1
                            
                    except Exception as e:
                        errors.append(f"Row {idx + 2}: {str(e)}")
                        if len(errors) > 10:  # Limit error messages
                            errors.append("... and more errors")
                            break
                        continue
                
                conn.commit()
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "duplicates": duplicates,
            "total_rows": len(df),
            "errors": errors[:10] if errors else []
        })
        
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"Failed to process file: {str(e)}"
        }), 500


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
