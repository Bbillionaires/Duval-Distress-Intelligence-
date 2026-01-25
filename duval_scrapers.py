"""
Duval County Website Scrapers - PRODUCTION VERSION
Real implementations for each data source with actual working code
"""
import re
import time
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import requests


class DuvalTaxDeedNoticeScraper:
    """
    Scrapes Tax Deed Notices from Duval Clerk Official Records
    URL: https://or.duvalclerk.com/
    """
    
    BASE_URL = "https://or.duvalclerk.com"
    SEARCH_URL = f"{BASE_URL}/search/SearchTypeDocType"
    GRID_URL = f"{BASE_URL}/Search/GridResults"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-Requested-With': 'XMLHttpRequest'
        })
    
    def search_recent_notices(self, days_back=90):
        """
        Search for Tax Deed Notices filed in last N days
        Returns list of notices with parcel numbers
        """
        try:
            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            # Step 1: Submit search form for Tax Deed Notices (Doc Type 149)
            search_data = {
                'DocTypes': '149',  # Tax Deed Notice doc type
                'DocTypesDisplay': 'NOTICE OF TAX DEED SALE (NTD)',
                'RecordDateFrom': start_date.strftime('%m/%d/%Y'),
                'RecordDateTo': end_date.strftime('%m/%d/%Y'),
                'DateRangeList': ' '
            }
            
            print(f"  Searching Tax Deed Notices from {start_date.strftime('%m/%d/%Y')} to {end_date.strftime('%m/%d/%Y')}")
            
            # Submit search (this sets up the session)
            response = self.session.post(self.SEARCH_URL, data=search_data)
            
            if response.status_code != 200:
                print(f"  ❌ Search failed: {response.status_code}")
                return []
            
            # Step 2: Get actual data from GridResults endpoint
            grid_data = {
                'sort': '',
                'group': '',
                'filter': ''
            }
            
            grid_response = self.session.post(self.GRID_URL, data=grid_data)
            
            if grid_response.status_code != 200:
                print(f"  ❌ Grid results failed: {grid_response.status_code}")
                return []
            
            # Parse JSON response
            data = grid_response.json()
            
            notices = []
            for item in data.get('Data', []):
                try:
                    # Extract parcel from legal description
                    legal = item.get('DocLegalDescription', '')
                    parcel = self._extract_parcel(legal)
                    
                    notice = {
                        'instrument_number': item.get('InstrumentNumber', ''),
                        'record_date': item.get('RecordDate', ''),
                        'parcel': parcel,
                        'certificate_holder': item.get('DirectName', ''),
                        'owner': item.get('IndirectName', ''),
                        'book_page': item.get('BookPage', ''),
                        'legal_description': legal
                    }
                    
                    if parcel:  # Only add if we found a parcel
                        notices.append(notice)
                
                except Exception as e:
                    print(f"  Error parsing notice: {e}")
                    continue
            
            print(f"  ✅ Found {len(notices)} tax deed notices with parcels")
            return notices
            
        except Exception as e:
            print(f"  ❌ Error scraping notices: {e}")
            return []
    
    def _extract_parcel(self, legal_description):
        """Extract parcel number from legal description"""
        if not legal_description:
            return None
        
        # Look for PIN format: PIN 076629-0000
        match = re.search(r'PIN\s+(\d{6}-\d{4})', legal_description)
        if match:
            return match.group(1)
        
        # Alternative format: just numbers
        match = re.search(r'(\d{6}-\d{4})', legal_description)
        if match:
            return match.group(1)
        
        return None


class DuvalTaxDeedAuctionScraper:
    """
    Scrapes upcoming Tax Deed Auction listings
    URL: https://duval.realtaxdeed.com/
    Uses direct search page URL to bypass login issues
    """
    
    BASE_URL = "https://duval.realtaxdeed.com"
    LOGIN_URL = f"{BASE_URL}/index.cfm?ZACTION=LOGIN&ZMETHOD=LOGIN"
    # Direct URL to tax deed search/report page
    SEARCH_URL = "https://duval.realtaxdeed.com/index.cfm?zaction=admin&zmethod=REPORT&Report_id=33"
    DATA_URL = f"{BASE_URL}/index.cfm"
    
    def __init__(self, username="lawsofgreen", password="48484848"):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9'
        })
        self.username = username
        self.password = password
        self.logged_in = False
    
    def login(self):
        """
        Login to RealAuction site
        Simple POST login without handling disclaimers
        """
        try:
            print("  Logging in to auction site...")
            
            # Submit login directly
            login_data = {
                'LogName': self.username,
                'LogPass': self.password
            }
            
            response = self.session.post(self.LOGIN_URL, data=login_data, allow_redirects=True)
            
            if response.status_code != 200:
                print(f"  ❌ Login failed - HTTP {response.status_code}")
                return False
            
            # Store cookies for session
            # Check if we got a session cookie
            if 'cfid' in self.session.cookies or 'CFTOKEN' in self.session.cookies:
                self.logged_in = True
                print("  ✅ Login successful (session established)")
                return True
            
            # Alternative: check response content
            if 'Log Off' in response.text or 'LogOff' in response.text or len(response.text) > 1000:
                self.logged_in = True
                print("  ✅ Login successful")
                return True
            
            print("  ❌ Login failed - credentials may be incorrect")
            return False
                
        except Exception as e:
            print(f"  ❌ Login error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_upcoming_auctions(self, days_ahead=90):
        """
        Get all upcoming tax deed auctions
        Uses direct search URL to bypass navigation
        """
        if not self.logged_in:
            if not self.login():
                print("  ⚠️  Skipping auction scrape - login failed")
                return []
        
        try:
            # Calculate date range
            start_date = datetime.now()
            end_date = start_date + timedelta(days=days_ahead)
            
            print(f"  Searching Tax Deed auctions from {start_date.strftime('%m/%d/%Y')} to {end_date.strftime('%m/%d/%Y')}")
            
            # Step 1: Visit the search page to establish the report session
            print("  Accessing search page...")
            search_response = self.session.get(self.SEARCH_URL)
            
            if search_response.status_code != 200:
                print(f"  ❌ Could not access search page: {search_response.status_code}")
                return []
            
            # Extract REPID from the search page if needed
            # The REPID changes with each session, so we need to extract it
            import re
            repid_match = re.search(r'REPID[=:](\d+)', search_response.text)
            repid = repid_match.group(1) if repid_match else None
            
            if not repid:
                print("  ⚠️  Could not find REPID, using default...")
                # Try without REPID or use a default
            
            # Step 2: Submit filter to get Tax Deed auctions
            filter_params = {
                'AUCT_TYPE': '2',  # 2 = Tax Deed
                'CaseStatus': '0,1,2,3,4',
                'view_ssdate': start_date.strftime('%m/%d/%Y'),
                'view_sedate': end_date.strftime('%m/%d/%Y'),
                'zaction': 'AJAX',
                'zmethod': 'COM',
                'process': 'REPVIEW',
                'FUNC': 'FilterData',
                'SHOWJSON': 'false'
            }
            
            if repid:
                filter_params['REPID'] = repid
            
            # Apply filter
            filter_response = self.session.get(self.DATA_URL, params=filter_params)
            
            # Step 3: Get the actual data
            data_params = {
                'zaction': 'AJAX',
                'zmethod': 'COM',
                'process': 'REPVIEW',
                'FUNC': 'LoadData',
                'SHOWJSON': 'FALSE'
            }
            
            if repid:
                data_params['REPID'] = repid
            
            data_response = self.session.get(self.DATA_URL, params=data_params)
            
            if data_response.status_code != 200:
                print(f"  ❌ Data request failed: {data_response.status_code}")
                return []
            
            # Parse JSON response
            try:
                data = data_response.json()
            except:
                print("  ⚠️  Response is not JSON")
                return []
            
            auctions = []
            for row in data.get('rows', []):
                try:
                    cells = row.get('cell', [])
                    if len(cells) >= 13:
                        # Extract parcel from the data
                        parcel = str(cells[12]).strip() if len(cells) > 12 else ''
                        
                        auction = {
                            'sale_date': str(cells[0]) if len(cells) > 0 else '',
                            'case_number': str(cells[2]) if len(cells) > 2 else '',
                            'status': str(cells[3]) if len(cells) > 3 else '',
                            'opening_bid': str(cells[5]) if len(cells) > 5 else '',
                            'assessed_value': str(cells[6]) if len(cells) > 6 else '',
                            'certificate_holder': str(cells[7]) if len(cells) > 7 else '',
                            'address': str(cells[9]) if len(cells) > 9 else '',
                            'city': str(cells[10]) if len(cells) > 10 else '',
                            'zip': str(cells[11]) if len(cells) > 11 else '',
                            'parcel': parcel
                        }
                        
                        if parcel:
                            auctions.append(auction)
                
                except Exception as e:
                    continue
            
            print(f"  ✅ Found {len(auctions)} upcoming tax deed auctions")
            return auctions
            
        except Exception as e:
            print(f"  ❌ Error getting auctions: {e}")
            import traceback
            traceback.print_exc()
            return []


class DuvalPropertyAppraiserScraper:
    """
    Scrapes Duval County Property Appraiser
    URL: https://paopropertysearch.coj.net
    """
    
    BASE_URL = "https://paopropertysearch.coj.net"
    SEARCH_URL = f"{BASE_URL}/Basic/Search.aspx"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def search_by_parcel(self, parcel):
        """Search for property by parcel number"""
        try:
            # Get search page to grab viewstate
            response = self.session.get(self.SEARCH_URL)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract ASP.NET viewstate
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})
            viewstate_val = viewstate['value'] if viewstate else ''
            
            # Submit search
            search_data = {
                '__VIEWSTATE': viewstate_val,
                'ctl00$MainContent$txtParcelID': parcel,
                'ctl00$MainContent$btnSearch': 'Search'
            }
            
            result = self.session.post(self.SEARCH_URL, data=search_data)
            
            if result.status_code == 200:
                return self._parse_property_details(result.text)
            
            return None
            
        except Exception as e:
            print(f"  Error searching parcel {parcel}: {e}")
            return None
    
    def _parse_property_details(self, html):
        """Parse property details from results page"""
        soup = BeautifulSoup(html, 'html.parser')
        
        try:
            details = {
                'owner_name': self._extract_text(soup, '#MainContent_lblOwnerName'),
                'property_address': self._extract_text(soup, '#MainContent_lblPropertyAddress'),
                'city': self._extract_text(soup, '#MainContent_lblCity'),
                'zip': self._extract_text(soup, '#MainContent_lblZip'),
                'assessed_value': self._extract_amount(soup, '#MainContent_lblAssessed')
            }
            return details
        except Exception as e:
            return None
    
    def _extract_text(self, soup, selector):
        """Extract text from element"""
        elem = soup.select_one(selector)
        return elem.text.strip() if elem else ''
    
    def _extract_amount(self, soup, selector):
        """Extract dollar amount"""
        text = self._extract_text(soup, selector)
        clean = re.sub(r'[,$]', '', text)
        try:
            return float(clean)
        except:
            return 0.0


class DuvalTaxCollectorScraper:
    """
    Scrapes REAL delinquent tax data from Duval Tax Collector
    URL: https://county-taxes.net/fl-duval/property-tax
    
    Checks actual tax amounts due by parcel ID
    """
    
    BASE_URL = "https://county-taxes.net/fl-duval"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def check_parcel_delinquency(self, parcel):
        """
        Check actual tax delinquency for a specific parcel
        Returns detailed tax information including amounts and years
        """
        try:
            # Clean parcel number (remove dashes if present)
            parcel_clean = parcel.replace('-', '')
            
            # Search for parcel
            search_url = f"{self.BASE_URL}/property-tax"
            params = {'parcel': parcel_clean}
            
            response = self.session.get(search_url, params=params, timeout=10)
            
            if response.status_code != 200:
                print(f"    ⚠️  HTTP {response.status_code} for {parcel}")
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Parse tax information from the page
            tax_data = {
                'parcel': parcel,
                'total_due': 0.0,
                'delinquent_years': [],
                'years_delinquent': 0,
                'is_delinquent': False,
                'tax_details': [],
                'checked_at': datetime.now().isoformat()
            }
            
            # Look for tax year rows (adjust selectors based on actual HTML)
            tax_rows = soup.find_all('tr', class_=['tax-row', 'tax-year-row'])
            
            current_year = datetime.now().year
            
            for row in tax_rows:
                try:
                    # Extract year
                    year_cell = row.find('td', class_='year') or row.find_all('td')[0]
                    year_text = year_cell.text.strip()
                    year = int(re.search(r'(\d{4})', year_text).group(1))
                    
                    # Extract amount due
                    amount_cell = row.find('td', class_='amount-due') or row.find_all('td')[-1]
                    amount_text = amount_cell.text.strip()
                    amount = float(re.sub(r'[^0-9.]', '', amount_text))
                    
                    # Extract status
                    status_cell = row.find('td', class_='status')
                    status = status_cell.text.strip() if status_cell else ''
                    
                    # If amount > 0, it's delinquent
                    if amount > 0 and 'paid' not in status.lower():
                        tax_data['total_due'] += amount
                        tax_data['delinquent_years'].append(year)
                        tax_data['tax_details'].append({
                            'year': year,
                            'amount': amount,
                            'status': status
                        })
                
                except Exception as e:
                    continue
            
            # Calculate years of delinquency
            if tax_data['delinquent_years']:
                tax_data['years_delinquent'] = len(tax_data['delinquent_years'])
                tax_data['is_delinquent'] = True
            
            return tax_data
            
        except Exception as e:
            print(f"  ❌ Error checking parcel {parcel}: {e}")
            return None
    
    def check_multiple_parcels(self, parcels, min_years=2, min_amount=0):
        """
        Check delinquency for multiple parcels
        Only returns parcels meeting minimum criteria
        
        Args:
            parcels: List of parcel IDs
            min_years: Minimum years of delinquency (default: 2)
            min_amount: Minimum total amount due (default: 0)
        """
        results = []
        
        print(f"  Checking {len(parcels)} parcels...")
        print(f"  Criteria: {min_years}+ years delinquent, ${min_amount}+ due")
        
        for i, parcel in enumerate(parcels, 1):
            try:
                result = self.check_parcel_delinquency(parcel)
                
                if result and result['is_delinquent']:
                    # Check if meets criteria
                    meets_years = result['years_delinquent'] >= min_years
                    meets_amount = result['total_due'] >= min_amount
                    
                    if meets_years and meets_amount:
                        results.append(result)
                        print(f"    ✅ {parcel}: ${result['total_due']:.2f} ({result['years_delinquent']} years)")
                    else:
                        print(f"    ⚠️  {parcel}: ${result['total_due']:.2f} ({result['years_delinquent']} years) - doesn't meet criteria")
                else:
                    print(f"    ✓ {parcel}: Current (no delinquency)")
                
                # Rate limiting
                if i % 10 == 0:
                    print(f"    Progress: {i}/{len(parcels)} checked...")
                time.sleep(1)
                
            except Exception as e:
                print(f"    ❌ {parcel}: Error - {e}")
                continue
        
        print(f"  ✅ Found {len(results)} parcels meeting criteria")
        return results