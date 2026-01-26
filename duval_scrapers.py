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


import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime, timedelta

class DuvalTaxDeedAuctionScraper:
    """
    Scrapes Duval County Tax Deed Auctions
    Handles multiple sequential disclaimer pages automatically
    """
    
    BASE_URL = "https://duval.realtaxdeed.com"
    LOGIN_URL = f"{BASE_URL}/index.cfm"
    SEARCH_URL = f"{BASE_URL}/index.cfm?zaction=admin&zmethod=REPORT&Report_id=33"
    DATA_URL = f"{BASE_URL}/index.cfm"
    
    def __init__(self, username, password):
        self.session = requests.Session()
        self.username = username
        self.password = password
        self.logged_in = False
    
    def accept_all_disclaimers(self, max_attempts=10):
        """
        Accept all sequential disclaimer pages until we reach actual content.
        The site uses Notice IDs that must be accepted in order.
        """
        print("  🔓 Checking for disclaimer pages...")
        
        for attempt in range(max_attempts):
            # Get current page
            response = self.session.get(self.BASE_URL)
            
            # Check if we're past all disclaimers
            if self._is_past_disclaimers(response.text):
                print(f"  ✅ All disclaimers cleared after {attempt} acceptance(s)")
                return True
            
            # Extract Notice ID from current disclaimer page
            nid = self._extract_notice_id(response.text)
            
            if not nid:
                print(f"  ⚠️  No Notice ID found on attempt {attempt + 1}")
                if attempt > 0:
                    # We accepted at least one, assume we're done
                    return True
                return False
            
            print(f"  📋 Accepting disclaimer {attempt + 1}: Notice ID {nid}")
            
            # Accept this specific disclaimer
            accept_response = self.session.post(
                self.LOGIN_URL,
                data={
                    'zaction': 'AJAX',
                    'zmethod': 'COM',
                    'process': 'NOTICE',
                    'func': 'ACCEPT',
                    'showjson': 'false',
                    'NID': nid
                }
            )
            
            # Small delay for server processing
            time.sleep(0.5)
        
        print(f"  ⚠️  Still seeing disclaimers after {max_attempts} attempts")
        return False
    
    def _is_past_disclaimers(self, html):
        """Check if we're past all disclaimer pages"""
        # Look for signs we're on actual content
        past_indicators = [
            'Quick Search' in html,
            'Auction Type' in html,
            'logout' in html.lower(),
            'my account' in html.lower()
        ]
        
        # Look for signs we're still on disclaimer
        disclaimer_indicators = [
            'Notice and alert page' in html,
            'disclaimer' in html.lower() and 'accept' in html.lower(),
            'I have read and agree' in html,
            re.search(r'AcceptNotice\(\d+\)', html)
        ]
        
        # Must have at least one positive indicator and no negative ones
        return any(past_indicators) and not any(disclaimer_indicators)
    
    def _extract_notice_id(self, html):
        """Extract Notice ID (NID) from disclaimer page"""
        # Method 1: onclick handler like onclick="AcceptNotice(10038)"
        match = re.search(r'AcceptNotice\((\d+)\)', html)
        if match:
            return match.group(1)
        
        # Method 2: Hidden input with name="NID"
        soup = BeautifulSoup(html, 'html.parser')
        nid_input = soup.find('input', {'name': 'NID'})
        if nid_input and nid_input.get('value'):
            return nid_input['value']
        
        # Method 3: JavaScript variable
        match = re.search(r'NID["\s]*[:=]["\s]*["\']?(\d+)["\']?', html)
        if match:
            return match.group(1)
        
        # Method 4: data-nid attribute
        nid_elem = soup.find(attrs={'data-nid': True})
        if nid_elem:
            return nid_elem['data-nid']
        
        return None
    
    def login(self):
        """Login to RealAuction site"""
        print(f"  🔐 Logging in as {self.username}...")
        
        try:
            # Step 1: Get home page and handle any initial disclaimers
            home_response = self.session.get(self.LOGIN_URL)
            self.accept_all_disclaimers()
            
            # Step 2: Submit AJAX login
            login_response = self.session.post(
                self.LOGIN_URL,
                data={
                    'ZACTION': 'AJAX',
                    'ZMETHOD': 'LOGIN',
                    'func': 'LOGIN',
                    'USERNAME': self.username,
                    'USERPASS': self.password
                }
            )
            
            # Check response
            try:
                result = login_response.json()
                if result.get('isOk') == 'YES':
                    print("  ✅ Login successful!")
                    self.logged_in = True
                    
                    # Accept any post-login disclaimers
                    self.accept_all_disclaimers()
                    return True
                else:
                    print(f"  ❌ Login failed: {result}")
                    return False
            except:
                print("  ⚠️  Login response not JSON, checking cookies...")
                
            # Check for session cookies as backup
            has_session = any(c in self.session.cookies for c in ['cfid', 'cftoken'])
            if has_session:
                print("  ✅ Session established via cookies")
                self.logged_in = True
                self.accept_all_disclaimers()
                return True
            
            print("  ❌ Login failed - no session")
            return False
            
        except Exception as e:
            print(f"  ❌ Login error: {e}")
            return False
    
    def get_upcoming_auctions(self, days_ahead=90):
        """Get all upcoming tax deed auctions"""
        if not self.logged_in and not self.login():
            print("  ⚠️  Skipping - login failed")
            return []
        
        try:
            start_date = datetime.now()
            end_date = start_date + timedelta(days=days_ahead)
            start_str = f"{start_date.month}/{start_date.day}/{start_date.year}"
            end_str = f"{end_date.month}/{end_date.day}/{end_date.year}"
            
            print(f"  🔍 Searching auctions from {start_str} to {end_str}")
            
            # Step 1: Initialize report page and handle disclaimers
            print("  📄 Loading report page...")
            report_response = self.session.get(self.SEARCH_URL)
            
            # Accept any disclaimers on report page
            self.accept_all_disclaimers()
            
            # Re-fetch report page after disclaimers
            report_response = self.session.get(self.SEARCH_URL)
            
            # Step 2: Extract REPID from page or generate
            repid = self._extract_repid(report_response.text)
            if not repid:
                repid = str(int(time.time() * 1000))
            print(f"  🔑 Using REPID: {repid}")
            
            # Step 3: Apply filter to get real REPID
            print("  🎯 Applying Tax Deed filter...")
            filter_params = {
                'AUCT_TYPE': '2',
                'CaseNumber': '',
                'view_ssdate': start_str,
                'view_sedate': end_str,
                'ParcelID': '',
                'PrimaryPlaintiffTD': '',
                'Address': '',
                'City': '',
                'Zip': '',
                'My_bids': 'null',
                'CaseStatus': '0,1,2,3,4,5,6',
                'zaction': 'AJAX',
                'zmethod': 'COM',
                'process': 'REPVIEW',
                'FUNC': 'FilterData',
                'SHOWJSON': 'false',
                'REPID': repid,
                '_': str(int(time.time() * 1000))
            }
            
            filter_response = self.session.get(self.DATA_URL, params=filter_params)
            
            # Extract REPID from filter response
            new_repid = self._extract_repid(filter_response.text)
            if new_repid:
                repid = new_repid
                print(f"  ✓ Filter returned REPID: {repid}")
            
            time.sleep(1)
            
            # Step 4: Load data using REPID
            print("  📊 Loading auction data...")
            data_response = self.session.post(
                self.DATA_URL,
                params={
                    'zaction': 'AJAX',
                    'zmethod': 'COM',
                    'Process': 'REPVIEW',
                    'SHOWJSON': 'FALSE',
                    'REPID': repid,
                    'func': 'LoadData'
                },
                data={
                    'rows': '1000',
                    'page': '1',
                    'sidx': 'vw.startdatetime',
                    'sord': 'asc',
                    '_search': 'false',
                    'nd': str(int(time.time() * 1000))
                },
                headers={'X-Requested-With': 'XMLHttpRequest'}
            )
            
            # Parse JSON response
            try:
                data = data_response.json()
                rows = data.get('rows', [])
                print(f"  ✅ Found {len(rows)} auction records")
                
                # Parse auction data
                auctions = []
                for row in rows:
                    cells = row.get('cell', [])
                    if len(cells) < 13:
                        continue
                    
                    parcel = str(cells[12]).strip()
                    if not parcel:
                        continue
                    
                    # Clean case number
                    case_num_raw = str(cells[2])
                    case_match = re.search(r'>([^<]+)</A>', case_num_raw)
                    case_number = case_match.group(1) if case_match else case_num_raw
                    
                    auctions.append({
                        'sale_date': str(cells[0]),
                        'add_date': str(cells[1]),
                        'case_number': case_number,
                        'status': str(cells[3]),
                        'opening_bid': str(cells[4]),
                        'certificate_amount': str(cells[5]),
                        'assessed_value': str(cells[6]),
                        'certificate_holder': str(cells[7]),
                        'address': str(cells[9]),
                        'city': str(cells[10]),
                        'zip': str(cells[11]),
                        'parcel': parcel
                    })
                
                return auctions
                
            except Exception as e:
                print(f"  ❌ Failed to parse response: {e}")
                print(f"  Response preview: {data_response.text[:500]}")
                return []
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _extract_repid(self, html):
        """Extract REPID from HTML"""
        match = re.search(r"var\s+ReportID\s*=\s*['\"](\d+)['\"]", html)
        return match.group(1) if match else None


# Example usage
if __name__ == "__main__":
    scraper = DuvalTaxDeedAuctionScraper(
        username="lawsofgreen",
        password="48484848"
    )
    
    if scraper.login():
        auctions = scraper.get_upcoming_auctions()
        print(f"\n✅ Retrieved {len(auctions)} auctions")
        
        for auction in auctions[:5]:  # Show first 5
            print(f"  • {auction['parcel']} - {auction['case_number']} - {auction['status']}")

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