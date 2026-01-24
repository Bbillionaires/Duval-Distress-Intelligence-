#!/usr/bin/env python3
"""
Automated Tax Lead Scraper System - SaaS Ready

Runs scheduled scraping jobs and stores results in database.
Can be triggered by:
  1. Cron job (daily/weekly)
  2. API endpoint (/api/trigger_scrape)
  3. Manual admin button

Perfect for deployment on Render with scheduled tasks.
"""
import os
import csv
import json
import re
import time
import base64
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup

# Configuration
BASE_DIR = Path(__file__).resolve().parent
LIENHUB_CSV = BASE_DIR / "duval_lienhub_x_live_zip_v3.csv"
DATABASE_URL = os.getenv("DATABASE_URL", "")

# County APIs
COUNTY_TAX_BASE = "https://county-taxes.net"
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
DUVAL_ALG_ENDPOINT = f"https://{DUVAL_ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"
CLERK_BASE = "https://or.duvalclerk.com"


def db_conn():
    """Get database connection"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    return psycopg2.connect(dsn)


def db_init():
    """Initialize database tables for automated scraping"""
    with db_conn() as conn:
        with conn.cursor() as cur:
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
            
            # Properties table - stores all scraped properties
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
            
            # Scrape history - track changes over time
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


class AutomatedScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.job_id = None
    
    # ========== JOB MANAGEMENT ==========
    
    def create_job(self, job_type):
        """Create a new scrape job"""
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                INSERT INTO scrape_jobs (job_type, status, started_at)
                VALUES (%s, 'running', NOW())
                RETURNING id
                """, (job_type,))
                self.job_id = cur.fetchone()[0]
        return self.job_id
    
    def update_job(self, status, properties_found=0, properties_updated=0, error_message=None):
        """Update job status"""
        if not self.job_id:
            return
        
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                UPDATE scrape_jobs
                SET status = %s,
                    completed_at = NOW(),
                    properties_found = %s,
                    properties_updated = %s,
                    error_message = %s
                WHERE id = %s
                """, (status, properties_found, properties_updated, error_message, self.job_id))
    
    # ========== SCRAPING METHODS ==========
    
    def scrape_sweet_spot_leads(self, batch_size=100, max_to_check=None):
        """
        Main scraping function - finds sweet spot leads
        This is what runs on schedule
        """
        print(f"\n{'='*70}")
        print(f"AUTOMATED SCRAPE JOB - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}")
        
        self.create_job('sweet_spot_leads')
        
        try:
            # Step 1: Load certificate properties
            properties = self._load_certificates()
            
            if not properties:
                self.update_job('completed', 0, 0, 'No certificate data found')
                return
            
            if max_to_check:
                properties = properties[:max_to_check]
            
            # Step 2: Verify and update each property
            updated_count = 0
            found_count = 0
            
            for i in range(0, len(properties), batch_size):
                batch = properties[i:i + batch_size]
                
                for prop in batch:
                    result = self._verify_and_store_property(prop)
                    if result:
                        found_count += 1
                        if result == 'updated':
                            updated_count += 1
                
                # Progress update
                print(f"  Progress: {min(i + batch_size, len(properties))}/{len(properties)} | Found: {found_count} | Updated: {updated_count}")
                time.sleep(2)
            
            # Step 3: Scrape tax deed notices
            self._scrape_and_store_ntd()
            
            # Mark job complete
            self.update_job('completed', found_count, updated_count)
            
            print(f"\n✓ Job completed: {found_count} properties, {updated_count} updated")
            
        except Exception as e:
            error_msg = str(e)
            print(f"\n❌ Job failed: {error_msg}")
            self.update_job('failed', 0, 0, error_msg)
            raise
    
    def _load_certificates(self):
        """Load certificate properties from CSV"""
        if not LIENHUB_CSV.exists():
            return []
        
        properties = []
        with LIENHUB_CSV.open('r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                parcel = row.get("lienhub_account_no", "").strip()
                if parcel:
                    properties.append({
                        "parcel": parcel,
                        "certificate_number": row.get("lienhub_certificate_number", ""),
                        "owner": row.get("lienhub_owner", ""),
                        "address": row.get("lienhub_situs_address", ""),
                        "city": row.get("lienhub_situs_city", ""),
                        "zip": row.get("lienhub_situs_zip", ""),
                        "face_amount": self._parse_float(row.get("lienhub_face_amount")),
                    })
        
        print(f"  Loaded {len(properties):,} certificate properties")
        return properties
    
    def _verify_and_store_property(self, prop):
        """Verify current status and store in database"""
        parcel = prop['parcel']
        
        # Search for property
        hit = self._search_parcel(parcel)
        if not hit:
            return None
        
        # Get current amounts
        public_url = hit.get("custom_parameters", {}).get("public_url", "")
        total_due, delinquent_due = self._fetch_amount_due(public_url)
        
        # Only store if still delinquent
        if not total_due or total_due <= 0:
            return None
        
        # Calculate certificate age
        cert_year = self._extract_year(prop.get('certificate_number'))
        cert_age_months = self._calculate_cert_age(cert_year) if cert_year else None
        
        # Determine stage
        if cert_age_months is None:
            stage = "unknown_age"
        elif cert_age_months < 6:
            stage = "early_delinquent"
        elif cert_age_months <= 24:
            stage = "sweet_spot"
        else:
            stage = "late_stage"
        
        # Store in database
        with db_conn() as conn:
            with conn.cursor() as cur:
                # Upsert property
                cur.execute("""
                INSERT INTO properties (
                    parcel, stage, certificate_number, certificate_year,
                    certificate_age_months, owner, address, city, zip,
                    face_amount, current_total_due, current_delinquent,
                    public_url, last_verified_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (parcel) DO UPDATE SET
                    stage = EXCLUDED.stage,
                    certificate_age_months = EXCLUDED.certificate_age_months,
                    current_total_due = EXCLUDED.current_total_due,
                    current_delinquent = EXCLUDED.current_delinquent,
                    last_verified_at = NOW(),
                    updated_at = NOW()
                """, (
                    parcel, stage, prop.get('certificate_number'), cert_year,
                    cert_age_months, prop.get('owner'), prop.get('address'),
                    prop.get('city'), prop.get('zip'), prop.get('face_amount'),
                    total_due, delinquent_due, COUNTY_TAX_BASE + public_url if public_url else None
                ))
                
                # Add to history
                cur.execute("""
                INSERT INTO property_history (parcel, total_due, delinquent_amount, stage)
                VALUES (%s, %s, %s, %s)
                """, (parcel, total_due, delinquent_due, stage))
        
        time.sleep(0.3)
        return 'updated'
    
    def _scrape_and_store_ntd(self):
        """Scrape tax deed notices and store in DB"""
        print(f"\n  Scraping tax deed notices...")
        
        notices = self._scrape_ntd(days_back=180)
        
        if not notices:
            return
        
        with db_conn() as conn:
            with conn.cursor() as cur:
                # Store notices
                for notice in notices:
                    cur.execute("""
                    INSERT INTO tax_deed_notices (parcel, doc_number, recorded_date, party_names, legal_description)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """, (
                        notice['parcel'], notice.get('doc_number'),
                        notice.get('recorded_date'), notice.get('party_names'),
                        notice.get('legal_description')
                    ))
                
                # Flag properties with NTD
                cur.execute("""
                UPDATE properties
                SET has_tax_deed_notice = TRUE,
                    stage = 'exiting_sweetspot'
                WHERE parcel IN (SELECT parcel FROM tax_deed_notices)
                """)
        
        print(f"  ✓ Stored {len(notices)} tax deed notices")
    
    # ========== HELPER METHODS (Same as before) ==========
    
    def _search_parcel(self, parcel):
        """Search Algolia for parcel"""
        headers = {
            "x-algolia-application-id": DUVAL_ALG_APP_ID,
            "x-algolia-api-key": DUVAL_ALG_API_KEY,
            "Content-Type": "application/json",
        }
        
        body = {"requests": [{"indexName": DUVAL_ALG_INDEX, "params": f"hitsPerPage=1&query={parcel}"}]}
        
        try:
            resp = self.session.post(DUVAL_ALG_ENDPOINT, headers=headers, data=json.dumps(body), timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            hits = results[0].get("hits", []) if results else []
            return hits[0] if hits else None
        except Exception:
            return None
    
    def _fetch_amount_due(self, public_url):
        """Fetch current amount due"""
        if not public_url:
            return None, None
        
        try:
            parsed = urlparse(public_url)
            qs = parse_qs(parsed.query)
            guid = qs.get("parcel", [None])[0]
            
            if guid:
                token_str = f"duval:real_estate:parents:{guid}"
                token_b64 = base64.b64encode(token_str.encode("utf-8")).decode("utf-8")
                iframe_url = f"https://county-taxes.net/iframe-taxsys/duval.county-taxes.com/govhub/property-tax/{token_b64}/load-amount-due"
                
                resp = self.session.get(iframe_url, timeout=12)
                
                if resp.ok:
                    text = " ".join(resp.text.split())
                    match = re.search(r"TOTAL\s+AMOUNT\s+DUE[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})", text, re.IGNORECASE)
                    total_due = float(match.group(1).replace(",", "")) if match else None
                    
                    delinq_match = re.search(r"DELINQUENT[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})", text, re.IGNORECASE)
                    delinquent = float(delinq_match.group(1).replace(",", "")) if delinq_match else None
                    
                    return total_due, delinquent
        except Exception:
            pass
        
        return None, None
    
    def _scrape_ntd(self, days_back):
        """Scrape NTD from clerk"""
        search_url = CLERK_BASE + "/search/SearchTypeDocType?Length=6"
        grid_url = CLERK_BASE + "/Search/PartialGrid"
        
        end_date = datetime.today()
        start_date = end_date - timedelta(days=days_back)
        
        try:
            resp = self.session.get(search_url, timeout=30)
            resp.raise_for_status()
            
            form_data = self._build_form_defaults(resp.text)
            form_data.update({
                "DocTypes": "NTD",
                "DocTypesDisplay_input": "NOTICE OF TAX DEED SALE (NTD)",
                "DocTypesDisplay": "NOTICE OF TAX DEED SALE (NTD)",
                "DateRangeList": " ",
                "RecordDateFrom": start_date.strftime("%m/%d/%Y"),
                "RecordDateTo": end_date.strftime("%m/%d/%Y")
            })
            
            resp = self.session.post(search_url, data=form_data, timeout=60)
            resp.raise_for_status()
            
            resp = self.session.get(grid_url, timeout=60)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            
            if not table:
                return []
            
            headers = [th.get_text(strip=True) for th in table.find_all('th')]
            results = []
            
            for tr in table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) != len(headers):
                    continue
                
                row = {h: td.get_text(" ", strip=True) for h, td in zip(headers, tds)}
                parcel = self._extract_parcel_from_row(row)
                
                if parcel:
                    results.append({
                        "parcel": parcel,
                        "doc_number": row.get("Doc #", ""),
                        "recorded_date": row.get("Recorded", ""),
                        "party_names": row.get("Party Names", ""),
                        "legal_description": row.get("Legal Description", ""),
                    })
            
            return results
        except Exception:
            return []
    
    def _build_form_defaults(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        form = soup.find('form', id='schfrm')
        data = {}
        if not form:
            return data
        for tag in form.find_all(['input', 'textarea', 'select']):
            name = tag.get('name')
            if name:
                value = tag.get('value', '') if tag.name != 'textarea' else tag.text or ''
                if name not in data:
                    data[name] = value or ''
        return data
    
    def _extract_parcel_from_row(self, row_data):
        for value in row_data.values():
            if value:
                match = re.search(r'\b\d{6}-\d{4}\b', value)
                if match:
                    return match.group(0)
                match = re.search(r'\b\d{10}\b', value)
                if match:
                    num = match.group(0)
                    return f"{num[:6]}-{num[6:]}"
        return ""
    
    def _extract_year(self, cert_number):
        if not cert_number:
            return None
        match = re.match(r'(\d{2})', str(cert_number))
        if match:
            year_2digit = int(match.group(1))
            return 2000 + year_2digit if year_2digit <= 50 else 1900 + year_2digit
        return None
    
    def _calculate_cert_age(self, cert_year):
        if not cert_year:
            return None
        cert_date = datetime(cert_year, 6, 1)
        now = datetime.now()
        months = (now.year - cert_date.year) * 12 + (now.month - cert_date.month)
        return max(0, months)
    
    def _parse_float(self, val):
        try:
            return float(str(val).replace(",", "").strip())
        except:
            return None


def run_scheduled_scrape():
    """Entry point for scheduled job"""
    scraper = AutomatedScraper()
    
    # For production: check all properties
    scraper.scrape_sweet_spot_leads()
    
    # For testing: limit to 100
    # scraper.scrape_sweet_spot_leads(max_to_check=100)


if __name__ == "__main__":
    # Initialize database on first run
    db_init()
    
    # Run scrape
    run_scheduled_scrape()
