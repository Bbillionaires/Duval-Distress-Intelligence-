#!/usr/bin/env python3
"""
Duval County Comprehensive Tax Lead Scraper
Covers all stages: Delinquent taxes -> Certificates -> Tax Deed Notices -> Auctions
"""
import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# Configuration
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "tax_leads"
OUTPUT_DIR.mkdir(exist_ok=True)

# Duval County URLs
COUNTY_TAX_BASE = "https://county-taxes.net"
CLERK_BASE = "https://or.duvalclerk.com"
AUCTION_BASE = "https://duval.realtaxdeed.com"

# Algolia config for live property search
DUVAL_ALG_APP_ID = "0LWZO52LS2"
DUVAL_ALG_API_KEY = "c0745578b56854a1b90ed57b63fbf0ba"
DUVAL_ALG_INDEX = "fl-duval.property_tax"
DUVAL_ALG_ENDPOINT = f"https://{DUVAL_ALG_APP_ID}-dsn.algolia.net/1/indexes/*/queries"


class DuvalTaxScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    # ========== STAGE 1: DELINQUENT PROPERTIES ==========
    
    def search_algolia_parcels(self, query="", filters="", hits_per_page=100):
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
        except Exception as e:
            print(f"Algolia search error: {e}")
            return []
    
    def fetch_property_amount_due(self, public_url):
        """Fetch current amount due from property page"""
        if not public_url:
            return None, None
        
        if not public_url.startswith("http"):
            public_url = COUNTY_TAX_BASE + public_url
        
        try:
            resp = self.session.get(public_url, timeout=20)
            resp.raise_for_status()
            
            # Look for "TOTAL AMOUNT DUE" pattern
            text = " ".join(resp.text.split())
            match = re.search(
                r"TOTAL\s+AMOUNT\s+DUE[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
                text, re.IGNORECASE
            )
            
            if match:
                total_due = float(match.group(1).replace(",", ""))
                
                # Try to find delinquent amount
                delinq_match = re.search(
                    r"DELINQUENT[^$0-9]{0,60}\$?\s*([0-9][\d,]*\.\d{2})",
                    text, re.IGNORECASE
                )
                delinquent = float(delinq_match.group(1).replace(",", "")) if delinq_match else None
                
                return total_due, delinquent
            
            return None, None
            
        except Exception as e:
            print(f"Error fetching amount for {public_url}: {e}")
            return None, None
    
    def scrape_delinquent_properties(self, min_due=1000, max_results=1000, zip_codes=None):
        """
        Scrape delinquent properties from Duval County
        Returns properties with outstanding tax amounts
        
        Args:
            min_due: Minimum amount owed to include
            max_results: Maximum number of properties to return
            zip_codes: Optional list of ZIP codes to filter by (e.g., ["32209", "32210"])
        """
        print(f"\n=== STAGE 1: Scraping Delinquent Properties (min ${min_due}) ===")
        
        results = []
        seen_parcels = set()
        
        # Search strategy: If zip codes provided, search by zip, otherwise search broadly
        search_terms = zip_codes if zip_codes else ["32"]  # "32" catches all Duval zips
        
        for search_term in search_terms:
            print(f"\nSearching ZIP: {search_term}")
            
            # Search Algolia
            hits = self.search_algolia_parcels(query=search_term, hits_per_page=100)
            
            if not hits:
                print(f"  No results for {search_term}")
                continue
            
            print(f"  Processing {len(hits)} properties...")
            
            for idx, hit in enumerate(hits):
                if len(results) >= max_results:
                    print(f"\n✓ Reached max_results limit ({max_results})")
                    return results
                
                parcel = hit.get("external_id") or hit.get("parcel", "")
                
                # Skip duplicates
                if parcel in seen_parcels:
                    continue
                seen_parcels.add(parcel)
                
                display_name = hit.get("display_name", "")
                
                custom_params = hit.get("custom_parameters", {})
                entities = custom_params.get("entities", [])
                
                owner = ""
                address = ""
                city = ""
                zip_code = ""
                
                if entities:
                    entity = entities[0]
                    owner = entity.get("name", "")
                    address = entity.get("address", "")
                    city = entity.get("city", "")
                    zip_code = entity.get("zip", "")
                
                public_url = custom_params.get("public_url", "")
                
                # Fetch live amount
                total_due, delinquent = self.fetch_property_amount_due(public_url)
                
                if total_due and total_due >= min_due:
                    results.append({
                        "parcel": parcel,
                        "owner": owner or display_name,
                        "address": address,
                        "city": city,
                        "zip": zip_code,
                        "total_due": total_due,
                        "delinquent_amount": delinquent,
                        "public_url": COUNTY_TAX_BASE + public_url if public_url else "",
                        "stage": "delinquent",
                        "scraped_date": datetime.now().isoformat()
                    })
                    
                    print(f"  [{len(results)}/{max_results}] Found: {parcel} - ${total_due:,.2f} due")
                
                # Progress indicator
                if (idx + 1) % 20 == 0:
                    print(f"    Processed {idx + 1}/{len(hits)} from this search...")
                
                time.sleep(0.3)  # Rate limiting
            
            time.sleep(1)  # Brief pause between searches
        
        print(f"\n✓ Found {len(results)} delinquent properties total")
        return results
    
    # ========== STAGE 2: TAX CERTIFICATES ==========
    
    def scrape_tax_certificates(self, year=None):
        """
        Scrape tax certificate sales data
        Tax certificates are sold when properties don't pay taxes
        """
        print(f"\n=== STAGE 2: Scraping Tax Certificates ===")
        
        if not year:
            year = datetime.now().year
        
        # Duval certificate sale info (you may need to adjust URL)
        cert_url = f"{COUNTY_TAX_BASE}/public_duval.county-taxes.com/certificates"
        
        results = []
        
        try:
            resp = self.session.get(cert_url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for certificate listings (structure varies by county)
            # This is a template - adjust selectors based on actual page structure
            cert_tables = soup.find_all('table')
            
            for table in cert_tables:
                rows = table.find_all('tr')
                
                for row in rows[1:]:  # Skip header
                    cols = row.find_all('td')
                    if len(cols) >= 4:
                        results.append({
                            "certificate_number": cols[0].get_text(strip=True),
                            "parcel": cols[1].get_text(strip=True),
                            "owner": cols[2].get_text(strip=True),
                            "amount": self._parse_currency(cols[3].get_text(strip=True)),
                            "stage": "certificate",
                            "year": year,
                            "scraped_date": datetime.now().isoformat()
                        })
            
            print(f"Found {len(results)} tax certificates")
            
        except Exception as e:
            print(f"Error scraping certificates: {e}")
        
        return results
    
    # ========== STAGE 3: TAX DEED NOTICES (NTD) ==========
    
    def scrape_tax_deed_notices(self, days_back=150):
        """
        Scrape Notice of Tax Deed Sale (NTD) from Duval Clerk of Courts
        These are properties heading to auction
        """
        print(f"\n=== STAGE 3: Scraping Tax Deed Notices (last {days_back} days) ===")
        
        search_url = CLERK_BASE + "/search/SearchTypeDocType?Length=6"
        grid_url = CLERK_BASE + "/Search/PartialGrid"
        
        # Date range
        end_date = datetime.today()
        start_date = end_date - timedelta(days=days_back)
        
        start_str = start_date.strftime("%m/%d/%Y")
        end_str = end_date.strftime("%m/%d/%Y")
        
        print(f"Searching from {start_str} to {end_str}")
        
        try:
            # Get initial page for form data
            resp = self.session.get(search_url, timeout=30)
            resp.raise_for_status()
            
            form_data = self._build_form_defaults(resp.text)
            
            # Set NTD search parameters
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
            
            # Fetch grid results
            resp = self.session.get(grid_url, timeout=60)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            
            if not table:
                print("No results table found")
                return []
            
            headers = [th.get_text(strip=True) for th in table.find_all('th')]
            results = []
            
            for tr in table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) != len(headers):
                    continue
                
                row = {h: td.get_text(" ", strip=True) for h, td in zip(headers, tds)}
                
                # Extract parcel number from legal description or other fields
                parcel = self._extract_parcel(row)
                
                results.append({
                    "parcel": parcel,
                    "document_number": row.get("Doc #", ""),
                    "recorded_date": row.get("Recorded", ""),
                    "party_names": row.get("Party Names", ""),
                    "legal_description": row.get("Legal Description", ""),
                    "stage": "tax_deed_notice",
                    "scraped_date": datetime.now().isoformat()
                })
            
            print(f"Found {len(results)} tax deed notices")
            return results
            
        except Exception as e:
            print(f"Error scraping tax deed notices: {e}")
            return []
    
    # ========== STAGE 4: TAX DEED AUCTIONS ==========
    
    def scrape_tax_deed_auctions(self):
        """
        Scrape upcoming tax deed auction listings
        Final stage before property sale
        """
        print(f"\n=== STAGE 4: Scraping Tax Deed Auctions ===")
        
        auction_url = f"{AUCTION_BASE}/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE="
        
        results = []
        
        try:
            # Get upcoming auction dates
            resp = self.session.get(AUCTION_BASE, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Find auction date links (adjust selector based on actual site)
            auction_links = soup.find_all('a', href=re.compile(r'AUCTIONDATE='))
            
            for link in auction_links:
                auction_date = link.get_text(strip=True)
                auction_href = link.get('href')
                
                if not auction_href.startswith('http'):
                    auction_href = urljoin(AUCTION_BASE, auction_href)
                
                print(f"Fetching auction for: {auction_date}")
                
                # Fetch auction details
                resp = self.session.get(auction_href, timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Parse property listings (adjust based on actual structure)
                prop_rows = soup.find_all('tr', class_=re.compile(r'prop|listing'))
                
                for row in prop_rows:
                    cols = row.find_all('td')
                    if len(cols) >= 4:
                        results.append({
                            "parcel": cols[0].get_text(strip=True),
                            "address": cols[1].get_text(strip=True),
                            "assessed_value": self._parse_currency(cols[2].get_text(strip=True)),
                            "opening_bid": self._parse_currency(cols[3].get_text(strip=True)),
                            "auction_date": auction_date,
                            "stage": "auction",
                            "scraped_date": datetime.now().isoformat()
                        })
                
                time.sleep(2)  # Rate limiting
            
            print(f"Found {len(results)} auction properties")
            
        except Exception as e:
            print(f"Error scraping auctions: {e}")
        
        return results
    
    # ========== HELPER METHODS ==========
    
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
    
    def _parse_currency(self, text):
        """Convert currency string to float"""
        try:
            cleaned = re.sub(r'[^\d.]', '', text)
            return float(cleaned) if cleaned else 0.0
        except:
            return 0.0
    
    def _extract_parcel(self, row_data):
        """Extract parcel number from row data"""
        # Try common field names
        for field in ['Parcel', 'Parcel #', 'Parcel ID', 'Legal Description']:
            if field in row_data:
                # Look for parcel pattern (e.g., 123456-7890)
                text = row_data[field]
                match = re.search(r'\d{6,}-?\d{4}', text)
                if match:
                    return match.group(0)
        
        return ""
    
    def save_results(self, results, filename_prefix):
        """Save results to CSV"""
        if not results:
            print(f"No results to save for {filename_prefix}")
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = OUTPUT_DIR / f"{filename_prefix}_{timestamp}.csv"
        
        fieldnames = list(results[0].keys())
        
        with filepath.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        
        print(f"✓ Saved {len(results)} records to: {filepath}")
    
    def scrape_all_stages(self, delinquent_min=1000, delinquent_max=500):
        """Run complete scraping pipeline for all stages"""
        print("\n" + "="*60)
        print("DUVAL COUNTY COMPREHENSIVE TAX LEAD SCRAPER")
        print("="*60)
        
        all_results = {
            'delinquent': [],
            'certificates': [],
            'notices': [],
            'auctions': []
        }
        
        # Stage 1: Delinquent properties
        all_results['delinquent'] = self.scrape_delinquent_properties(
            min_due=delinquent_min,
            max_results=delinquent_max
        )
        self.save_results(all_results['delinquent'], 'delinquent_properties')
        
        # Stage 2: Tax certificates
        all_results['certificates'] = self.scrape_tax_certificates()
        self.save_results(all_results['certificates'], 'tax_certificates')
        
        # Stage 3: Tax deed notices
        all_results['notices'] = self.scrape_tax_deed_notices(days_back=150)
        self.save_results(all_results['notices'], 'tax_deed_notices')
        
        # Stage 4: Auctions
        all_results['auctions'] = self.scrape_tax_deed_auctions()
        self.save_results(all_results['auctions'], 'auction_properties')
        
        # Create combined master file
        self._create_master_file(all_results)
        
        print("\n" + "="*60)
        print("SCRAPING COMPLETE")
        print("="*60)
        print(f"Delinquent: {len(all_results['delinquent'])}")
        print(f"Certificates: {len(all_results['certificates'])}")
        print(f"Notices: {len(all_results['notices'])}")
        print(f"Auctions: {len(all_results['auctions'])}")
        print(f"Total: {sum(len(v) for v in all_results.values())}")
        
        return all_results
    
    def _create_master_file(self, all_results):
        """Combine all results into master file"""
        combined = []
        
        for stage, records in all_results.items():
            combined.extend(records)
        
        if combined:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = OUTPUT_DIR / f"master_tax_leads_{timestamp}.csv"
            
            # Get all unique fieldnames
            fieldnames = set()
            for record in combined:
                fieldnames.update(record.keys())
            
            fieldnames = sorted(list(fieldnames))
            
            with filepath.open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(combined)
            
            print(f"\n✓ Master file saved: {filepath}")


def main():
    scraper = DuvalTaxScraper()
    
    # Run complete scraping pipeline
    results = scraper.scrape_all_stages(
        delinquent_min=1000,  # Minimum amount due
        delinquent_max=500    # Max properties to scrape
    )
    
    # Or run individual stages:
    # scraper.scrape_delinquent_properties(min_due=1000, max_results=100)
    # scraper.scrape_tax_certificates()
    # scraper.scrape_tax_deed_notices(days_back=90)
    # scraper.scrape_tax_deed_auctions()


if __name__ == "__main__":
    main()
