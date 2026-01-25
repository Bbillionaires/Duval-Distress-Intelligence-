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


def verify_tax_delinquency(min_years=2, min_amount=1000, limit=50):
    """
    Verify tax delinquency for existing parcels in database
    
    Args:
        min_years: Minimum years of delinquency to qualify (default: 2)
        min_amount: Minimum total amount due to qualify (default: $1000)
        limit: Max parcels to check per run (default: 50)
    """
    print(f"\n💰 Verifying Tax Delinquency (≥{min_years} years, ≥${min_amount})...")
    
    job_id = create_scrape_job("tax_delinquency_verification")
    properties_checked = 0
    properties_updated = 0
    
    try:
        scraper = DuvalTaxCollectorScraper()
        
        # Get parcels that need verification
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT parcel
                    FROM properties 
                    WHERE parcel IS NOT NULL 
                    AND parcel != ''
                    AND (last_verified_at IS NULL 
                         OR last_verified_at < NOW() - INTERVAL '7 days')
                    LIMIT %s
                """, (limit,))
                parcels_to_check = [row['parcel'] for row in cur.fetchall()]
        
        if not parcels_to_check:
            print("  No parcels need verification")
            update_scrape_job(job_id, "completed", 0, 0)
            return 0
        
        print(f"  Checking {len(parcels_to_check)} parcels...")
        
        # Check parcels for delinquency
        results = scraper.check_multiple_parcels(
            parcels_to_check,
            min_years=min_years,
            min_amount=min_amount
        )
        
        properties_checked = len(parcels_to_check)
        
        # Update database with results
        with db_conn() as conn:
            with conn.cursor() as cur:
                for result in results:
                    try:
                        # Determine stage based on delinquency
                        if result['years_delinquent'] >= 3:
                            stage = 'danger_zone'
                        elif result['years_delinquent'] >= 2:
                            stage = 'sweet_spot'
                        else:
                            stage = 'pre_lien'
                        
                        # Update property with tax info
                        cur.execute("""
                            UPDATE properties
                            SET current_total_due = %s,
                                current_delinquent = %s,
                                stage = CASE 
                                    WHEN stage = 'auction' THEN 'auction'
                                    WHEN stage = 'tax_deed_filed' THEN 'tax_deed_filed'
                                    ELSE %s
                                END,
                                certificate_age_months = %s,
                                last_verified_at = NOW(),
                                updated_at = NOW()
                            WHERE parcel = %s
                        """, (
                            result['total_due'],
                            result['total_due'],
                            stage,
                            result['years_delinquent'] * 12,  # Convert years to months
                            result['parcel']
                        ))
                        
                        properties_updated += 1
                        
                    except Exception as e:
                        print(f"  Error updating {result['parcel']}: {e}")
                        continue
                
                conn.commit()
        
        print(f"✅ Verified {properties_checked} parcels")
        print(f"✅ Updated {properties_updated} delinquent properties")
        
        update_scrape_job(job_id, "completed", properties_checked, properties_updated)
        return properties_updated
        
    except Exception as e:
        print(f"❌ Error verifying tax delinquency: {e}")
        update_scrape_job(job_id, "failed", properties_checked, properties_updated, str(e))
        return 0
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


def run_full_scrape(min_delinquent_years=2, min_delinquent_amount=1000):
    """
    Run complete scraping workflow
    
    Args:
        min_delinquent_years: Minimum years of tax delinquency (default: 2)
        min_delinquent_amount: Minimum $ amount delinquent (default: $1000)
    """
    print("=" * 70)
    print("🚀 DUVAL COUNTY TAX LEAD SCRAPER - LIVE DATA")
    print("=" * 70)
    print(f"Started at: {datetime.now()}")
    print(f"Lead Criteria: {min_delinquent_years}+ years, ${min_delinquent_amount}+ past due")
    print()
    
    total_found = 0
    total_updated = 0
    
    # 1. Scrape tax deed notices (NO LOGIN)
    found = scrape_tax_deed_notices()
    total_found += found
    
    # 2. Scrape tax deed auction (WITH LOGIN) 
    # Skip if login fails, continue with other sources
    try:
        found = scrape_tax_deed_auction()
        total_found += found
    except Exception as e:
        print(f"⚠️  Skipping auction (login failed): {e}")
    
    # 3. Verify tax delinquency on existing parcels (REAL DATA!)
    updated = verify_tax_delinquency(
        min_years=min_delinquent_years,
        min_amount=min_delinquent_amount,
        limit=50  # Check 50 parcels per run
    )
    total_updated += updated
    
    # 4. Enrich with property appraiser data (NO LOGIN, limited batch)
    updated = enrich_with_property_appraiser()
    total_updated += updated
    
    # 5. Update all property stages
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
    print("💡 Your Leads (Properties Meeting Criteria):")
    print(f"   • {min_delinquent_years}+ years delinquent")
    print(f"   • ${min_delinquent_amount}+ past due")
    print("   • Check your dashboard to see them!")
    print()
    print("🔧 To Change Lead Criteria:")
    print(f"   python3 automated_scraper.py --min-years 3 --min-amount 5000")
    print()


if __name__ == "__main__":
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Duval County Tax Lead Scraper')
    parser.add_argument('--min-years', type=int, default=2,
                       help='Minimum years of tax delinquency (default: 2)')
    parser.add_argument('--min-amount', type=float, default=1000,
                       help='Minimum dollar amount delinquent (default: 1000)')
    
    args = parser.parse_args()
    
    try:
        run_full_scrape(
            min_delinquent_years=args.min_years,
            min_delinquent_amount=args.min_amount
        )
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)