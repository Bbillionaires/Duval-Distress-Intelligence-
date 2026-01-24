"""
Automated Duval County Tax Lead Scraper - PRODUCTION VERSION
Live scraping from all Duval County sources

Scrapes:
1. Tax Deed Notices - Official Records (NO LOGIN)
2. Tax Deed Auction - RealAuction site (WITH LOGIN)
3. Property Appraiser - Enrichment data (NO LOGIN)
4. Tax Collector - Delinquency verification (NO LOGIN)
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
import psycopg2.extras

# Import our custom scrapers
from duval_scrapers import (
    DuvalTaxDeedNoticeScraper,
    DuvalTaxDeedAuctionScraper,
    DuvalPropertyAppraiserScraper,
    DuvalTaxCollectorScraper
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Stage classification thresholds
SWEET_SPOT_MIN_MONTHS = 24  # 2 years
SWEET_SPOT_MAX_MONTHS = 36  # 3 years


def db_conn():
    """Connect to database"""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    dsn = DATABASE_URL
    if "sslmode=" not in dsn:
        dsn = dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    return psycopg2.connect(dsn)


def create_scrape_job(job_type):
    """Create a new scrape job record"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO scrape_jobs (job_type, status, started_at)
                VALUES (%s, 'running', NOW())
                RETURNING id
            """, (job_type,))
            job_id = cur.fetchone()[0]
            conn.commit()
            return job_id


def update_scrape_job(job_id, status, properties_found=0, properties_updated=0, error_message=None):
    """Update scrape job status"""
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
            """, (status, properties_found, properties_updated, error_message, job_id))
            conn.commit()


def classify_stage(has_tax_deed_notice, is_in_auction, total_due):
    """
    Classify property stage
    
    Stages:
    - current: No delinquency
    - pre_lien: Some delinquency but no notice
    - sweet_spot: Has some delinquency (BEST ROI)
    - tax_deed_filed: Tax deed notice filed
    - auction: In tax deed auction
    """
    if is_in_auction:
        return "auction"
    
    if has_tax_deed_notice:
        return "tax_deed_filed"
    
    if total_due <= 0:
        return "current"
    
    if total_due > 0:
        return "sweet_spot"  # Any delinquency is opportunity
    
    return "pre_lien"


def scrape_tax_deed_notices():
    """Scrape Tax Deed Notices from Official Records"""
    print("\n📋 Scraping Tax Deed Notices...")
    
    job_id = create_scrape_job("tax_deed_notices")
    notices_found = 0
    properties_updated = 0
    
    try:
        scraper = DuvalTaxDeedNoticeScraper()
        
        # Get notices from last 90 days
        notices = scraper.search_recent_notices(days_back=90)
        notices_found = len(notices)
        
        print(f"✅ Found {notices_found} tax deed notices")
        
        # Insert notices and update properties
        with db_conn() as conn:
            with conn.cursor() as cur:
                for notice in notices:
                    try:
                        parcel = notice.get('parcel')
                        if not parcel:
                            continue
                        
                        # Insert/update notice record
                        cur.execute("""
                            INSERT INTO tax_deed_notices (
                                parcel, doc_number, recorded_date,
                                party_names, legal_description
                            ) VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                        """, (
                            parcel,
                            notice.get('instrument_number', ''),
                            notice.get('record_date', ''),
                            notice.get('certificate_holder', ''),
                            notice.get('legal_description', '')
                        ))
                        
                        # Update property to mark has_tax_deed_notice
                        cur.execute("""
                            INSERT INTO properties (
                                parcel, stage, owner, has_tax_deed_notice,
                                last_verified_at, created_at
                            ) VALUES (
                                %s, 'tax_deed_filed', %s, TRUE, NOW(), NOW()
                            )
                            ON CONFLICT (parcel) DO UPDATE SET
                                has_tax_deed_notice = TRUE,
                                stage = 'tax_deed_filed',
                                owner = COALESCE(EXCLUDED.owner, properties.owner),
                                last_verified_at = NOW(),
                                updated_at = NOW()
                        """, (parcel, notice.get('owner', '')))
                        
                        properties_updated += 1
                        
                    except Exception as e:
                        print(f"  Error processing notice: {e}")
                        continue
                
                conn.commit()
        
        print(f"✅ Updated {properties_updated} properties with tax deed notices")
        update_scrape_job(job_id, "completed", notices_found, properties_updated)
        return notices_found
        
    except Exception as e:
        print(f"❌ Error scraping tax deed notices: {e}")
        update_scrape_job(job_id, "failed", notices_found, properties_updated, str(e))
        return 0


def scrape_tax_deed_auction():
    """Scrape upcoming Tax Deed Auction listings"""
    print("\n⚖️  Scraping Tax Deed Auction listings...")
    
    job_id = create_scrape_job("tax_deed_auction")
    auctions_found = 0
    properties_updated = 0
    
    try:
        # Initialize scraper with credentials
        scraper = DuvalTaxDeedAuctionScraper(
            username="lawsofgreen",
            password="48484848"
        )
        
        # Get auctions for next 90 days
        auctions = scraper.get_upcoming_auctions(days_ahead=90)
        auctions_found = len(auctions)
        
        print(f"✅ Found {auctions_found} properties in auction")
        
        # Update properties with auction status
        with db_conn() as conn:
            with conn.cursor() as cur:
                for auction in auctions:
                    try:
                        parcel = auction.get('parcel', '').strip()
                        if not parcel:
                            continue
                        
                        cur.execute("""
                            INSERT INTO properties (
                                parcel, stage, owner, address, city, zip,
                                certificate_number, last_verified_at, created_at
                            ) VALUES (
                                %s, 'auction', '', %s, %s, %s, %s, NOW(), NOW()
                            )
                            ON CONFLICT (parcel) DO UPDATE SET
                                stage = 'auction',
                                address = COALESCE(EXCLUDED.address, properties.address),
                                city = COALESCE(EXCLUDED.city, properties.city),
                                zip = COALESCE(EXCLUDED.zip, properties.zip),
                                certificate_number = EXCLUDED.certificate_number,
                                last_verified_at = NOW(),
                                updated_at = NOW()
                        """, (
                            parcel,
                            auction.get('address', ''),
                            auction.get('city', ''),
                            auction.get('zip', ''),
                            auction.get('case_number', '')
                        ))
                        
                        properties_updated += 1
                        
                    except Exception as e:
                        print(f"  Error updating auction property: {e}")
                        continue
                
                conn.commit()
        
        print(f"✅ Updated {properties_updated} properties in auction")
        update_scrape_job(job_id, "completed", auctions_found, properties_updated)
        return auctions_found
        
    except Exception as e:
        print(f"❌ Error scraping auction: {e}")
        update_scrape_job(job_id, "failed", auctions_found, properties_updated, str(e))
        return 0


def enrich_with_property_appraiser():
    """Enrich existing properties with Property Appraiser data"""
    print("\n🏠 Enriching with Property Appraiser data...")
    
    job_id = create_scrape_job("property_appraiser_enrichment")
    properties_updated = 0
    
    try:
        scraper = DuvalPropertyAppraiserScraper()
        
        # Get properties that need enrichment (limit to avoid overwhelming)
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT parcel
                    FROM properties 
                    WHERE (owner IS NULL OR owner = '' OR city IS NULL)
                    AND parcel IS NOT NULL AND parcel != ''
                    LIMIT 25
                """)
                properties_to_enrich = cur.fetchall()
        
        print(f"Found {len(properties_to_enrich)} properties to enrich")
        
        for prop in properties_to_enrich:
            try:
                print(f"  Looking up {prop['parcel']}...")
                details = scraper.search_by_parcel(prop['parcel'])
                
                if details:
                    with db_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE properties
                                SET owner = COALESCE(NULLIF(%s, ''), owner),
                                    address = COALESCE(NULLIF(%s, ''), address),
                                    city = COALESCE(NULLIF(%s, ''), city),
                                    zip = COALESCE(NULLIF(%s, ''), zip),
                                    updated_at = NOW()
                                WHERE parcel = %s
                            """, (
                                details.get('owner_name', ''),
                                details.get('property_address', ''),
                                details.get('city', ''),
                                details.get('zip', ''),
                                prop['parcel']
                            ))
                            conn.commit()
                    
                    properties_updated += 1
                    print(f"    ✅ Enriched")
                
                time.sleep(2)  # Rate limiting
                
            except Exception as e:
                print(f"  Error enriching {prop['parcel']}: {e}")
                continue
        
        update_scrape_job(job_id, "completed", len(properties_to_enrich), properties_updated)
        return properties_updated
        
    except Exception as e:
        print(f"❌ Error enriching properties: {e}")
        update_scrape_job(job_id, "failed", 0, properties_updated, str(e))
        return 0


def update_property_stages():
    """Update all property stages based on current data"""
    print("\n🔄 Recalculating property stages...")
    
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get all properties
            cur.execute("""
                SELECT 
                    id, parcel, current_total_due,
                    has_tax_deed_notice, stage
                FROM properties
            """)
            properties = cur.fetchall()
            
            updated = 0
            for prop in properties:
                # Determine new stage
                is_in_auction = (prop.get('stage') == 'auction')
                has_ntd = prop.get('has_tax_deed_notice', False)
                total_due = prop.get('current_total_due', 0) or 0
                
                new_stage = classify_stage(has_ntd, is_in_auction, total_due)
                
                # Update stage
                cur.execute("""
                    UPDATE properties 
                    SET stage = %s, 
                        updated_at = NOW()
                    WHERE id = %s
                """, (new_stage, prop['id']))
                
                updated += 1
            
            conn.commit()
            print(f"✅ Recalculated stages for {updated} properties")
            
            # Show breakdown
            cur.execute("""
                SELECT stage, COUNT(*) as count
                FROM properties
                GROUP BY stage
                ORDER BY count DESC
            """)
            breakdown = cur.fetchall()
            
            print("\n📊 Properties by Stage:")
            for row in breakdown:
                print(f"  {row['stage']:15} {row['count']:5} properties")
            
            return updated


def run_full_scrape():
    """Run complete scraping workflow"""
    print("=" * 70)
    print("🚀 DUVAL COUNTY TAX LEAD SCRAPER - LIVE DATA")
    print("=" * 70)
    print(f"Started at: {datetime.now()}")
    print()
    
    total_found = 0
    total_updated = 0
    
    # 1. Scrape tax deed notices (NO LOGIN)
    found = scrape_tax_deed_notices()
    total_found += found
    
    # 2. Scrape tax deed auction (WITH LOGIN)
    found = scrape_tax_deed_auction()
    total_found += found
    
    # 3. Enrich with property appraiser data (NO LOGIN, limited batch)
    updated = enrich_with_property_appraiser()
    total_updated += updated
    
    # 4. Update all property stages
    updated = update_property_stages()
    total_updated += updated
    
    print()
    print("=" * 70)
    print("✅ SCRAPING COMPLETE")
    print("=" * 70)
    print(f"Total new properties found: {total_found}")
    print(f"Total properties updated: {total_updated}")
    print(f"Completed at: {datetime.now()}")
    print()
    print("💡 Next Steps:")
    print("  1. Set up daily cron job to run this scraper")
    print("  2. Monitor scrape_jobs table for status")
    print("  3. Review properties by stage in your dashboard")
    print()


if __name__ == "__main__":
    try:
        run_full_scrape()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
