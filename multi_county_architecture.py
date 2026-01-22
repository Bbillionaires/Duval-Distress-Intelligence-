"""
Real Estate Intelligence Platform - Multi-County SaaS
Architecture for scalable county-by-county expansion

Each county gets its own:
- Data scrapers
- Database tables
- API endpoints
- Pricing tier

Users subscribe to specific counties or bundles
"""
import os
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, make_response
from werkzeug.security import generate_password_hash, check_password_hash
import secrets


# ============================================================================
# COUNTY REGISTRY - Add new counties here
# ============================================================================

@dataclass
class CountyConfig:
    """Configuration for each county"""
    code: str                    # e.g., "duval", "miami-dade"
    name: str                    # Display name
    state: str                   # FL, GA, etc.
    tax_collector_url: str       # County tax website
    clerk_url: str              # Clerk of courts
    algolia_app_id: str         # If using Algolia
    algolia_api_key: str
    algolia_index: str
    pricing_tier: int           # 1=Basic ($49), 2=Pro ($99), 3=Premium ($199)
    enabled: bool = True


# Registry of all supported counties
COUNTIES = {
    "duval": CountyConfig(
        code="duval",
        name="Duval County",
        state="FL",
        tax_collector_url="https://county-taxes.net",
        clerk_url="https://or.duvalclerk.com",
        algolia_app_id="0LWZO52LS2",
        algolia_api_key="c0745578b56854a1b90ed57b63fbf0ba",
        algolia_index="fl-duval.property_tax",
        pricing_tier=1
    ),
    "miami-dade": CountyConfig(
        code="miami-dade",
        name="Miami-Dade County",
        state="FL",
        tax_collector_url="https://www.miamidade.gov/pa/",
        clerk_url="https://www.miami-dadeclerk.com",
        algolia_app_id="",  # To be configured
        algolia_api_key="",
        algolia_index="",
        pricing_tier=2,
        enabled=False  # Not yet implemented
    ),
    "broward": CountyConfig(
        code="broward",
        name="Broward County",
        state="FL",
        tax_collector_url="https://www.broward.org/Revenue/Collections/",
        clerk_url="https://www.browardclerk.org",
        algolia_app_id="",
        algolia_api_key="",
        algolia_index="",
        pricing_tier=2,
        enabled=False
    ),
    # Add more counties here...
}


# ============================================================================
# PRICING TIERS
# ============================================================================

PRICING_TIERS = {
    1: {"name": "Basic", "price_per_county": 49, "counties": ["duval"]},
    2: {"name": "Professional", "price_per_county": 99, "counties": ["miami-dade", "broward"]},
    3: {"name": "Premium", "price_per_county": 199, "counties": ["all_florida"]},
}


# Subscription packages
PACKAGES = {
    "starter": {
        "name": "Starter",
        "price": 49,
        "counties": ["duval"],
        "features": ["Daily updates", "Sweet spot leads", "Export CSV"]
    },
    "professional": {
        "name": "Professional",
        "price": 149,
        "counties": ["duval", "miami-dade"],
        "features": ["Daily updates", "Sweet spot leads", "Export CSV", "Historical tracking", "2 counties"]
    },
    "enterprise": {
        "name": "Enterprise",
        "price": 399,
        "counties": list(COUNTIES.keys()),
        "features": ["Daily updates", "Sweet spot leads", "Export CSV", "Historical tracking", "All counties", "Priority support", "API access"]
    },
    "custom": {
        "name": "Custom",
        "price": 0,  # Calculated per county
        "counties": [],  # User selects
        "features": ["Choose your counties", "Pay per county"]
    }
}


# ============================================================================
# DATABASE SCHEMA - Multi-County
# ============================================================================

def db_init_multi_county():
    """Initialize database with multi-county support"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            # Users table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              email TEXT UNIQUE NOT NULL,
              pw_hash TEXT NOT NULL,
              is_admin BOOLEAN NOT NULL DEFAULT FALSE,
              subscription_package TEXT,
              subscription_status TEXT DEFAULT 'trial',
              subscription_expires_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            
            # User county access
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_counties (
              id SERIAL PRIMARY KEY,
              user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              county_code TEXT NOT NULL,
              granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(user_id, county_code)
            );
            CREATE INDEX IF NOT EXISTS idx_user_counties_user ON user_counties(user_id);
            """)
            
            # Properties table - NOW WITH COUNTY CODE
            cur.execute("""
            CREATE TABLE IF NOT EXISTS properties (
              id SERIAL PRIMARY KEY,
              county_code TEXT NOT NULL,
              parcel TEXT NOT NULL,
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
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(county_code, parcel)
            );
            CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county_code);
            CREATE INDEX IF NOT EXISTS idx_properties_stage ON properties(county_code, stage);
            CREATE INDEX IF NOT EXISTS idx_properties_parcel ON properties(parcel);
            """)
            
            # Scrape jobs - per county
            cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_jobs (
              id SERIAL PRIMARY KEY,
              county_code TEXT NOT NULL,
              job_type TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              started_at TIMESTAMPTZ,
              completed_at TIMESTAMPTZ,
              properties_found INT DEFAULT 0,
              properties_updated INT DEFAULT 0,
              error_message TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_scrape_jobs_county ON scrape_jobs(county_code);
            """)
            
            # Sessions
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TIMESTAMPTZ NOT NULL
            );
            """)
            
            # Property history
            cur.execute("""
            CREATE TABLE IF NOT EXISTS property_history (
              id SERIAL PRIMARY KEY,
              county_code TEXT NOT NULL,
              parcel TEXT NOT NULL,
              total_due NUMERIC(12,2),
              delinquent_amount NUMERIC(12,2),
              stage TEXT,
              snapshot_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_history_county_parcel ON property_history(county_code, parcel);
            """)
            
            # Tax deed notices
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tax_deed_notices (
              id SERIAL PRIMARY KEY,
              county_code TEXT NOT NULL,
              parcel TEXT NOT NULL,
              doc_number TEXT,
              recorded_date TEXT,
              party_names TEXT,
              legal_description TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ntd_county_parcel ON tax_deed_notices(county_code, parcel);
            """)


# ============================================================================
# COUNTY-SPECIFIC SCRAPERS
# ============================================================================

class CountyScraper:
    """Base class for county scrapers"""
    
    def __init__(self, county_config: CountyConfig):
        self.county = county_config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def scrape_sweet_spot_leads(self):
        """Override in county-specific scraper"""
        raise NotImplementedError(f"Scraper not implemented for {self.county.name}")
    
    def scrape_tax_deed_notices(self):
        """Override in county-specific scraper"""
        raise NotImplementedError(f"Tax deed scraper not implemented for {self.county.name}")


class DuvalScraper(CountyScraper):
    """Duval County specific implementation"""
    
    def scrape_sweet_spot_leads(self):
        # Your existing Duval scraper code here
        pass
    
    def scrape_tax_deed_notices(self):
        # Your existing NTD scraper here
        pass


class MiamiDadeScraper(CountyScraper):
    """Miami-Dade County specific implementation"""
    
    def scrape_sweet_spot_leads(self):
        # Miami-Dade specific logic
        # Different website structure, different APIs
        pass


# Scraper registry
SCRAPERS = {
    "duval": DuvalScraper,
    "miami-dade": MiamiDadeScraper,
    # Add more as you implement them
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def db_conn():
    """Get database connection"""
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    return psycopg2.connect(dsn)


def get_user_counties(user_id: int) -> List[str]:
    """Get list of counties user has access to"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT county_code FROM user_counties WHERE user_id = %s
            """, (user_id,))
            return [row[0] for row in cur.fetchall()]


def grant_county_access(user_id: int, county_code: str):
    """Grant user access to a county"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO user_counties (user_id, county_code)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """, (user_id, county_code))


def calculate_subscription_price(county_codes: List[str]) -> int:
    """Calculate total price for selected counties"""
    total = 0
    for code in county_codes:
        county = COUNTIES.get(code)
        if county:
            tier = PRICING_TIERS.get(county.pricing_tier, {})
            total += tier.get("price_per_county", 0)
    return total


# ============================================================================
# FLASK APP - Multi-County API
# ============================================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("APP_SECRET", "dev-secret")


@app.get("/api/counties")
def api_counties():
    """List all available counties"""
    counties_list = [
        {
            "code": c.code,
            "name": c.name,
            "state": c.state,
            "pricing_tier": c.pricing_tier,
            "price": PRICING_TIERS.get(c.pricing_tier, {}).get("price_per_county", 0),
            "enabled": c.enabled
        }
        for c in COUNTIES.values()
    ]
    return jsonify({"ok": True, "counties": counties_list})


@app.get("/api/packages")
def api_packages():
    """List subscription packages"""
    return jsonify({"ok": True, "packages": PACKAGES})


@app.get("/api/properties/<county_code>")
def api_properties_by_county(county_code):
    """Get properties for a specific county"""
    # Check user has access to this county
    user = session_user()
    if not user:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    user_counties = get_user_counties(user["id"])
    if county_code not in user_counties and not user["is_admin"]:
        return jsonify({"ok": False, "error": f"No access to {county_code}"}), 403
    
    # Get filters
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
    where_clauses = ["county_code = %s"]
    params = [county_code]
    
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
    
    where_sql = "WHERE " + " AND ".join(where_clauses)
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Count
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
        "county": county_code,
        "total": total,
        "page": page,
        "page_size": page_size,
        "returned": len(rows),
        "rows": rows
    })


@app.get("/api/stats/<county_code>")
def api_stats_by_county(county_code):
    """Get statistics for a specific county"""
    user = session_user()
    if not user:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    user_counties = get_user_counties(user["id"])
    if county_code not in user_counties and not user["is_admin"]:
        return jsonify({"ok": False, "error": f"No access to {county_code}"}), 403
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Overall counts for this county
            cur.execute("""
            SELECT 
                COUNT(*) as total_properties,
                COUNT(*) FILTER (WHERE stage = 'sweet_spot') as sweet_spot_count,
                COUNT(*) FILTER (WHERE has_tax_deed_notice = TRUE) as with_ntd,
                SUM(current_total_due) as total_amount_due
            FROM properties
            WHERE county_code = %s
            """, (county_code,))
            overall = cur.fetchone()
            
            # By stage
            cur.execute("""
            SELECT stage, COUNT(*) as count
            FROM properties
            WHERE county_code = %s
            GROUP BY stage
            ORDER BY count DESC
            """, (county_code,))
            by_stage = cur.fetchall()
    
    return jsonify({
        "ok": True,
        "county": county_code,
        "overall": overall,
        "by_stage": by_stage
    })


@app.get("/api/my-counties")
def api_my_counties():
    """Get counties the current user has access to"""
    user = session_user()
    if not user:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    user_counties = get_user_counties(user["id"])
    
    counties_data = []
    for code in user_counties:
        county = COUNTIES.get(code)
        if county:
            counties_data.append({
                "code": county.code,
                "name": county.name,
                "state": county.state
            })
    
    return jsonify({
        "ok": True,
        "counties": counties_data
    })


@app.post("/api/subscribe")
def api_subscribe():
    """Subscribe user to counties (would integrate with Stripe)"""
    user = session_user()
    if not user:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    data = request.get_json() or {}
    package = data.get("package")
    custom_counties = data.get("counties", [])
    
    # Get counties to grant
    if package and package in PACKAGES:
        counties_to_grant = PACKAGES[package]["counties"]
        price = PACKAGES[package]["price"]
    elif custom_counties:
        counties_to_grant = custom_counties
        price = calculate_subscription_price(custom_counties)
    else:
        return jsonify({"ok": False, "error": "Invalid subscription"}), 400
    
    # TODO: Integrate with Stripe payment here
    # For now, just grant access
    
    for county_code in counties_to_grant:
        if county_code in COUNTIES:
            grant_county_access(user["id"], county_code)
    
    return jsonify({
        "ok": True,
        "message": f"Subscribed to {len(counties_to_grant)} counties",
        "counties": counties_to_grant,
        "price": price
    })


@app.post("/api/trigger_scrape/<county_code>")
def api_trigger_scrape_county(county_code):
    """Trigger scrape for specific county (admin only)"""
    user = session_user()
    if not user or not user["is_admin"]:
        return jsonify({"ok": False, "error": "Not authorized"}), 401
    
    if county_code not in COUNTIES:
        return jsonify({"ok": False, "error": "Invalid county"}), 400
    
    county = COUNTIES[county_code]
    
    if not county.enabled:
        return jsonify({"ok": False, "error": f"{county.name} not yet available"}), 400
    
    # TODO: Trigger background scrape job for this county
    
    return jsonify({
        "ok": True,
        "message": f"Scrape started for {county.name}"
    })


def session_user():
    """Get current session user (stub - implement from your auth code)"""
    return None


if __name__ == "__main__":
    db_init_multi_county()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
