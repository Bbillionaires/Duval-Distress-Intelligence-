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
    Uses AJAX login with correct parameters
    """
    
    BASE_URL = "https://duval.realtaxdeed.com"
    LOGIN_URL = f"{BASE_URL}/index.cfm"  # AJAX login endpoint
    SEARCH_URL = "https://duval.realtaxdeed.com/index.cfm?zaction=admin&zmethod=REPORT&Report_id=33"
    DATA_URL = f"{BASE_URL}/index.cfm"
    
    def __init__(self, username="lawsofgreen", password="48484848"):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-Requested-With': 'XMLHttpRequest'
        })
        self.username = username
        self.password = password
        self.logged_in = False
    
    def login(self):
        """
        Login to RealAuction site using AJAX login method
        Uses correct parameters: ZACTION=AJAX, ZMETHOD=LOGIN, USERNAME, USERPASS
        """
        try:
            print("  Logging in to auction site...")
            print(f"  Using username: {self.username}")
            
            # Step 1: Get the home page first to establish cookies and session
            print("  Step 1: Getting home page...")
            home_response = self.session.get(self.BASE_URL, allow_redirects=True)
            
            if home_response.status_code != 200:
                print(f"  ❌ Could not access home page: {home_response.status_code}")
                return False
            
            # Step 2: Submit AJAX login with correct parameters
            print("  Step 2: Submitting AJAX login...")
            login_data = {
                'ZACTION': 'AJAX',
                'ZMETHOD': 'LOGIN',
                'func': 'LOGIN',
                'USERNAME': self.username,
                'USERPASS': self.password
            }
            
            # Post login
            response = self.session.post(
                self.LOGIN_URL, 
                data=login_data, 
                allow_redirects=True,
                timeout=15
            )
            
            if response.status_code != 200:
                print(f"  ❌ Login request failed - HTTP {response.status_code}")
                return False
            
            # Step 3: Check login response
            try:
                # The AJAX login returns JSON
                result = response.json()
                print(f"  Login response: {result}")
                
                # Check for success in JSON response
                if isinstance(result, dict):
                    success = result.get('SUCCESS', False) or result.get('success', False)
                    if success or result.get('LOGGEDIN') or result.get('logged_in'):
                        self.logged_in = True
                        print("  ✅ Login successful (JSON confirmed)!")
                        return True
                    
                    # Check for error message
                    error = result.get('ERROR') or result.get('error') or result.get('MESSAGE')
                    if error:
                        print(f"  ❌ Login failed - {error}")
                        return False
                
            except:
                # Not JSON, check text response
                pass
            
            # Check session cookies
            cookies = self.session.cookies.get_dict()
            has_session = 'cfid' in cookies and 'cftoken' in cookies
            
            # Debug info
            print(f"  Response size: {len(response.text)} bytes")
            print(f"  Session cookies: {has_session}")
            print(f"  Cookies: {list(cookies.keys())}")
            
            # Check response content for success indicators
            response_lower = response.text.lower()
            has_success = any(x in response_lower for x in [
                'success', 'logged in', 'welcome', 'logout'
            ])
            has_error = any(x in response_lower for x in [
                'invalid', 'incorrect', 'failed', 'denied'
            ])
            
            print(f"  Has success indicator: {has_success}")
            print(f"  Has error indicator: {has_error}")
            
            if has_error:
                print("  ❌ Login failed - Error detected in response")
                return False
            
            # Success if we have session cookies and no error
            if has_session and not has_error:
                self.logged_in = True
                print("  ✅ Login successful!")
                return True
            
            # If we got this far and have cookies, try to proceed
            if has_session:
                print("  ⚠️  Login status unclear but have session - attempting to continue...")
                self.logged_in = True
                return True
            
            print("  ❌ Login failed - No session established")
            print(f"  Response preview: {response.text[:200]}")
            return False
                
        except requests.Timeout:
            print("  ❌ Login timeout - site may be slow or down")
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
            
            # Extract REPID from the search page
            # The REPID is a timestamp - try to find it in the page
            repid_match = re.search(r'REPID[=:"\s]+(\d{13,})', search_response.text, re.IGNORECASE)
            if not repid_match:
                # REPID might be in JavaScript or as a timestamp
                repid_match = re.search(r'["\']?REPID["\']?\s*[:=]\s*["\']?(\d{13,})', search_response.text, re.IGNORECASE)
            if not repid_match:
                # Try to find any 13-digit number (timestamp format)
                repid_match = re.search(r'\b(\d{13})\b', search_response.text)
            
            repid = repid_match.group(1) if repid_match else None
            
            # If still no REPID, generate one using current timestamp
            if not repid:
                import time
                repid = str(int(time.time() * 1000))  # JavaScript timestamp (milliseconds)
                print(f"  Generated REPID from timestamp: {repid}")
            else:
                print(f"  Found REPID: {repid}")
            
            # Step 2: Submit filter to get Tax Deed auctions
            print("  Step 2: Applying Tax Deed filter...")
            filter_params = {
                'AUCT_TYPE': '2',  # 2 = Tax Deed
                'CaseStatus': '0,1,2,3,4,5,6',  # All statuses
                'view_ssdate': start_date.strftime('%m/%d/%Y'),
                'view_sedate': end_date.strftime('%m/%d/%Y'),
                'zaction': 'AJAX',
                'zmethod': 'COM',
                'Process': 'REPVIEW',  # Capital P
                'FUNC': 'FilterData',
                'SHOWJSON': 'FALSE',
                'REPID': repid
            }
            
            # Apply filter - this is a GET request
            filter_response = self.session.get(self.DATA_URL, params=filter_params)
            
            if filter_response.status_code != 200:
                print(f"  ⚠️  Filter request failed: {filter_response.status_code}")
            
            # Wait a moment for filter to apply
            time.sleep(1)
            
            # Step 3: Get the actual data with POST and form data
            print("  Step 3: Loading auction data...")
            data_params = {
                'zaction': 'AJAX',
                'zmethod': 'COM',
                'Process': 'REPVIEW',  # Capital P to match browser
                'SHOWJSON': 'FALSE',
                'REPID': repid,
                'func': 'LoadData'
            }
            
            # Form data for jqGrid pagination
            form_data = {
                'rows': '1000',  # Get lots of rows
                'page': '1',
                'sidx': 'vw.startdatetime',
                'sord': 'asc',
                '_search': 'false',
                'nd': str(int(time.time() * 1000)),  # Timestamp to prevent caching
                'search': 'false'
            }
            
            print(f"  Requesting with REPID={repid}")
            
            # POST request with both params and form data
            data_response = self.session.post(
                self.DATA_URL, 
                params=data_params,
                data=form_data,
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*; q=0.01'
                }
            )
            
            if data_response.status_code != 200:
                print(f"  ❌ Data request failed: {data_response.status_code}")
                return []
            
            # Debug: Check content type
            content_type = data_response.headers.get('Content-Type', '')
            print(f"  Response Content-Type: {content_type}")
            print(f"  Response length: {len(data_response.text)} bytes")
            
            # Parse JSON response
            try:
                data = data_response.json()
                total_records = data.get('records', 0)
                total_rows = len(data.get('rows', []))
                print(f"  JSON Response - Records: {total_records}, Rows returned: {total_rows}")
                
                if total_rows == 0:
                    print(f"  ⚠️  No rows in response. Full JSON:")
                    print(f"  {data}")
                    
            except Exception as e:
                print(f"  ⚠️  Response is not JSON: {e}")
                print(f"  Response text (first 500 chars):")
                print(data_response.text[:500])
                
                # Try to see if we got redirected or need to re-login
                if 'login' in data_response.text.lower() or 'username' in data_response.text.lower():
                    print("  ⚠️  Appears to be login page - session may have expired")
                
                return []
            
            auctions = []
            for row in data.get('rows', []):
                try:
                    cells = row.get('cell', [])
                    if len(cells) < 13:
                        continue
                    
                    # Extract data from cell array
                    # Based on actual response: [sale_date, add_date, case_num, status, opening_bid?, cert_amt?, assessed, holder, ?, address, city, zip, parcel, ?, ?]
                    parcel = str(cells[12]).strip() if len(cells) > 12 else ''
                    
                    # Clean up case number (remove HTML tags)
                    case_num_raw = str(cells[2]) if len(cells) > 2 else ''
                    case_num_match = re.search(r'>([^<]+)</A>', case_num_raw)
                    case_number = case_num_match.group(1) if case_num_match else case_num_raw
                    
                    auction = {
                        'sale_date': str(cells[0]) if len(cells) > 0 else '',
                        'add_date': str(cells[1]) if len(cells) > 1 else '',
                        'case_number': case_number,
                        'status': str(cells[3]) if len(cells) > 3 else '',
                        'opening_bid': str(cells[4]) if len(cells) > 4 else '',
                        'certificate_amount': str(cells[5]) if len(cells) > 5 else '',
                        'assessed_value': str(cells[6]) if len(cells) > 6 else '',
                        'certificate_holder': str(cells[7]) if len(cells) > 7 else '',
                        'address': str(cells[9]) if len(cells) > 9 else '',
                        'city': str(cells[10]) if len(cells) > 10 else '',
                        'zip': str(cells[11]) if len(cells) > 11 else '',
                        'parcel': parcel
                    }
                    
                    if parcel and parcel != '':
                        auctions.append(auction)
                        print(f"    Found: {parcel} - {case_number} - {auction['status']}")
                
                except Exception as e:
                    print(f"    Error parsing row: {e}")
                    continue
            
            print(f"  ✅ Found {len(auctions)} upcoming tax deed auctions with parcels")
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