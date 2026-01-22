#!/usr/bin/env python3
"""
Duval County Enhanced Tax Lead Scraper
Works with your existing delinquent CSV and adds fresh data
"""
import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
import base64

import requests
from bs4 import BeautifulSoup

# Configuration
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "tax_leads"
OUTPUT_DIR.mkdir(exist_ok=True)

# Your existing delinquent CSV
EXISTING_DELINQ_CSV = BASE_DIR / "duval_delinquent_leads_big.csv"

# URLs
COUNTY_TAX_BASE = "https://county-taxes.net"
CLERK_BASE = "https://or.duvalclerk.com"
AUCTION_BASE = "https://duval.realtaxdeed.com"

# Algolia config
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
DUVAL_ALG_ENDPOINT = f"https://{DUVAL_ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"


class DuvalTaxLeadScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    # ========== STAGE 1: USE EXISTING DELINQUENT DATA ==========
    
    def load_existing_delinquent_data(self):
        """Load your existing delinquent CSV"""
        print(f"\n=== STAGE 1: Loading Existing Delinquent Data ===")
        
        if not EXISTING_DELINQ_CSV.exists():
            print(f"❌ File not found: {EXISTING_DELINQ_CSV}")
            return []
        
        results = []
        with EXISTING_DELINQ_CSV.open('r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                results.append({
                    "parcel": row.get("lienhub_account_no", ""),
                    "owner": row.get("lienhub_owner", ""),
                    "address": row.get("lienhub_situs_address", ""),
                    "city": row.get("lienhub_situs_city", ""),
                    "zip": row.get("lienhub_situs_zip", ""),
                    "face_amount": row.get("lienhub_face_amount", ""),
                    "total_due": row.get("live_total_due", ""),
                    "still_delinquent": row.get("still_delinquent", ""),
                    "certificate_number": row.get("lienhub_certificate_number", ""),
                    "stage": "delinquent",
                    "source": "existing_csv",
                    "scraped_date": datetime.now().isoformat()
                })
        
        print(f"✓ Loaded {len(results)} existing delinquent properties")
        return results
    
    def verify_live_amounts(self, parcels, max_to_check=50):
        """
        For a sample of parcels, verify their current amounts due
        """
        print(f"\n=== Verifying Live Amounts (checking {min(len(parcels), max_to_check)} properties) ===")
        
        verified = []
        
        for i, parcel_id in enumerate(parcels[:max_to_check]):
            if i > 0 and i % 10 == 0:
                print(f"  Checked {i}/{min(len(parcels), max_to_check)}...")
            
            # Search Algolia for this parcel
            hits = self.search_algolia_parcels(query=parcel_id, hits_per_page=1)
            
            if not hits:
                continue
            
            hit = hits[0]
            custom_params = hit.get("custom_parameters", {})
            public_url = custom_params.get("public_url", "")
            
            if public_url:
                total_due, delinquent = self.fetch_property_amount_due(public_url)
                
                if total_due:
                    verified.append({
                        "parcel": parcel_id,
                        "total_due_verified": total_due,
                        "delinquent_amount": delinquent,
                        "verified_date": datetime.now().isoformat()
                    })
                    
                    print(f"    {parcel_id}: ${total_due:,.2f}")
            
            time.sleep(0.5)
        
        print(f"✓ Verified {len(verified)} properties with current amounts")
        return verified
    
    # ========== STAGE 2: TAX DEED NOTICES (NTD) ==========
    
    def scrape_tax_deed_notices(self, days_back=150):
        """
        Scrape Notice of Tax Deed Sale (NTD) from Duval Clerk
        """
        print(f"\n=== STAGE 2: Scraping Tax Deed Notices (last {days_back} days) ===")
        
        search_url = CLERK_BASE + "/search/SearchTypeDocType?Length=6"
        grid_url = CLERK_BASE + "/Search/PartialGrid"
        
        end_date = datetime.today()
        start_date = end_date - timedelta(days=days_back)
        
        start_str = start_date.strftime("%m/%d/%Y")
        end_str = end_date.strftime("%m/%d/%Y")
        
        print(f"Searching from {start_str} to {end_str}")
        
        try:
            # Get initial page
            resp = self.session.get(search_url, timeout=30)
            resp.raise_for_status()
            
            form_data = self._build_form_defaults(resp.text)
            
            # Set NTD search
            form_data.update({
                "DocTypes": "NTD",
                "DocTypesDisplay_input": "NOTICE OF TAX DEED SALE (NTD)",
                "DocTypesDisplay": "NOTICE OF TAX DEED SALE (NTD)",
                "DateRangeList": " ",
                "RecordDateFrom": start_str,
                "RecordDateTo": end_str
            })
            
            # Submit search
            resp = self.session.post(search_url, data=form_data, timeout=60)
            resp.raise_for_status()
            
            # Get results grid
            resp = self.session.get(grid_url, timeout=60)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            
            if not table:
                print("  No results table found")
                return []
            
            headers = [th.get_text(strip=True) for th in table.find_all('th')]
            results = []
            
            for tr in table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) != len(headers):
                    continue
                
                row = {h: td.get_text(" ", strip=True) for h, td in zip(headers, tds)}
                
                # Extract parcel from data
                parcel = self._extract_parcel_from_clerk(row)
                
                results.append({
                    "parcel": parcel,
                    "document_number": row.get("Doc #", ""),
                    "recorded_date": row.get("Recorded", ""),
                    "party_names": row.get("Party Names", ""),
                    "legal_description": row.get("Legal Description", ""),
                    "stage": "tax_deed_notice",
                    "scraped_date": datetime.now().isoformat()
                })
            
            print(f"✓ Found {len(results)} tax deed notices")
            return results
            
        except Exception as e:
            print(f"❌ Error scraping tax deed notices: {e}")
            return []
    
    # ========== STAGE 3: TAX DEED AUCTIONS ==========
    
    def scrape_tax_deed_auctions(self):
        """
        Scrape upcoming tax deed auction listings
        """
        print(f"\n=== STAGE 3: Scraping Tax Deed Auctions ===")
        
        results = []
        
        try:
            # Get main auction page
            resp = self.session.get(AUCTION_BASE, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for "View Auctions" or similar links
            auction_links = soup.find_all('a', href=re.compile(r'auction|preview', re.I))
            
            print(f"  Found {len(auction_links)} potential auction links")
            
            for link in auction_links[:5]:  # Check first 5 links
                href = link.get('href', '')
                
                if not href:
                    continue
                
                if not href.startswith('http'):
                    href = urljoin(AUCTION_BASE, href)
                
                print(f"  Checking: {href}")
                
                try:
                    resp = self.session.get(href, timeout=30)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    # Look for property listings in tables
                    tables = soup.find_all('table')
                    
                    for table in tables:
                        rows = table.find_all('tr')
                        
                        for row in rows[1:]:  # Skip header
                            cols = row.find_all(['td', 'th'])
                            
                            if len(cols) >= 3:
                                # Extract data (adjust indices based on actual table structure)
                                row_text = [col.get_text(strip=True) for col in cols]
                                
                                # Look for parcel-like numbers
                                parcel = ""
                                for text in row_text:
                                    if re.match(r'\d{6,}-?\d{4}', text):
                                        parcel = text
                                        break
                                
                                if parcel:
                                    results.append({
                                        "parcel": parcel,
                                        "auction_info": " | ".join(row_text),
                                        "auction_url": href,
                                        "stage": "auction",
                                        "scraped_date": datetime.now().isoformat()
                                    })
                    
                    time.sleep(2)
                    
                except Exception as e:
                    print(f"    Error fetching {href}: {e}")
                    continue
            
            print(f"✓ Found {len(results)} auction properties")
            
        except Exception as e:
            print(f"❌ Error scraping auctions: {e}")
        
        return results
    
    # ========== HELPER METHODS ==========
    
    def search_algolia_parcels(self, query="", filters="", hits_per_page=20):
        """Search Duval property tax database via Algolia"""
        headers = {
            "x-algolia-application-id": DUVAL_ALG_APP_ID,
            "x-algolia-api-key": DUVAL_ALG_API_KEY,
            "Content-Type": "application/json",
        }
        
        body = {
            "requests": [{
                "indexName": DUVAL_ALG_INDEX,
                "params": f"hitsPerPage={hits_per_page}&query={query}",
            }]
        }
        
        if filters:
            body["requests"][0]["params"] += f"&filters={filters}"
        
        try:
            resp = self.session.post(DUVAL_ALG_ENDPOINT, headers=headers, 
                                    data=json.dumps(body), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            return results[0].get("hits", []) if results else []
        except Exception:
            return []
    
    def fetch_property_amount_due(self, public_url):
        """Fetch current amount due from property page"""
        if not public_url:
            return None, None
        
        if not public_url.startswith("http"):
            public_url = COUNTY_TAX_BASE + public_url
        
        try:
            # Try iframe endpoint first (faster)
            parsed = urlparse(public_url)
            qs = parse_qs(parsed.query)
            guid = qs.get("parcel", [None])[0]
            
            if guid:
                token_str = f"duval:real_estate:parents:{guid}"
                token_b64 = base64.b64encode(token_str.encode("utf-8")).decode("utf-8")
                
                iframe_url = (
                    "https://county-taxes.net/iframe-taxsys/duval.county-taxes.com/"
                    f"govhub/property-tax/{token_b64}/load-amount-due"
                )
                
                resp = self.session.get(iframe_url, timeout=15)
                
                if resp.ok:
                    text = " ".join(resp.text.split())
                    match = re.search(
                        r"TOTAL\s+AMOUNT\s+DUE[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
                        text, re.IGNORECASE
                    )
                    
                    if match:
                        total_due = float(match.group(1).replace(",", ""))
                        
                        delinq_match = re.search(
                            r"DELINQUENT[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
                            text, re.IGNORECASE
                        )
                        delinquent = float(delinq_match.group(1).replace(",", "")) if delinq_match else None
                        
                        return total_due, delinquent
            
            return None, None
            
        except Exception:
            return None, None
    
    def _build_form_defaults(self, html):
        """Extract form field defaults from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        form = soup.find('form', id='schfrm')
        
        data = {}
        if not form:
            return data
        
        for tag in form.find_all(['input', 'textarea', 'select']):
            name = tag.get('name')
            if not name:
                continue
            
            if tag.name == 'textarea':
                value = tag.text or tag.get('value', '')
            else:
                value = tag.get('value', '')
            
            if name not in data:
                data[name] = value or ''
        
        return data
    
    def _extract_parcel_from_clerk(self, row_data):
        """Extract parcel number from clerk row data"""
        # Check all fields for parcel pattern
        for field_name, value in row_data.items():
            if not value:
                continue
            
            # Look for standard Duval parcel format: 123456-7890
            match = re.search(r'\b\d{6}-\d{4}\b', value)
            if match:
                return match.group(0)
            
            # Try without dash
            match = re.search(r'\b\d{10}\b', value)
            if match:
                num = match.group(0)
                return f"{num[:6]}-{num[6:]}"
        
        return ""
    
    def save_results(self, results, filename_prefix):
        """Save results to CSV"""
        if not results:
            print(f"  No results to save for {filename_prefix}")
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = OUTPUT_DIR / f"{filename_prefix}_{timestamp}.csv"
        
        # Get all fieldnames
        fieldnames = set()
        for r in results:
            fieldnames.update(r.keys())
        fieldnames = sorted(list(fieldnames))
        
        with filepath.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        
        print(f"  ✓ Saved {len(results)} records → {filepath.name}")
        return filepath
    
    def run_full_pipeline(self, verify_sample=20):
        """Run complete scraping pipeline"""
        print("\n" + "="*70)
        print(" DUVAL COUNTY TAX LEAD SCRAPER - ENHANCED ")
        print("="*70)
        
        all_results = {}
        
        # Stage 1: Load existing delinquent data
        all_results['delinquent'] = self.load_existing_delinquent_data()
        
        if all_results['delinquent'] and verify_sample > 0:
            # Verify a sample of live amounts
            sample_parcels = [r['parcel'] for r in all_results['delinquent'][:verify_sample]]
            verified = self.verify_live_amounts(sample_parcels, max_to_check=verify_sample)
            
            if verified:
                self.save_results(verified, 'verified_amounts')
        
        # Stage 2: Fresh tax deed notices
        all_results['notices'] = self.scrape_tax_deed_notices(days_back=150)
        
        # Stage 3: Auction properties
        all_results['auctions'] = self.scrape_tax_deed_auctions()
        
        # Save all results
        print(f"\n{'='*70}")
        print(" SAVING RESULTS ")
        print(f"{'='*70}")
        
        for stage, records in all_results.items():
            if records:
                self.save_results(records, stage)
        
        # Create summary
        print(f"\n{'='*70}")
        print(" SUMMARY ")
        print(f"{'='*70}")
        print(f"  Delinquent Properties: {len(all_results.get('delinquent', []))}")
        print(f"  Tax Deed Notices:      {len(all_results.get('notices', []))}")
        print(f"  Auction Properties:    {len(all_results.get('auctions', []))}")
        print(f"  TOTAL:                 {sum(len(v) for v in all_results.values())}")
        print(f"{'='*70}\n")
        
        return all_results


def main():
    scraper = DuvalTaxLeadScraper()
    
    # Run full pipeline
    # Set verify_sample=0 to skip verification (faster)
    # Set verify_sample=50 to check 50 properties (slower but more accurate)
    results = scraper.run_full_pipeline(verify_sample=20)
    
    print("✓ Complete! Check the 'tax_leads' folder for output files.")


if __name__ == "__main__":
    main()
