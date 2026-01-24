"""
Duval County Website Scrapers
Specific implementations for each data source
"""
import re
import time
from datetime import datetime
from bs4 import BeautifulSoup
import requests


class DuvalTaxCollectorScraper:
    """
    Scrapes Duval County Tax Collector for delinquent taxes
    URL: https://www.duvalclerk.com/real-estate-taxes/search
    """
    
    BASE_URL = "https://www.duvalclerk.com"
    SEARCH_URL = f"{BASE_URL}/real-estate-taxes/search"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def search_delinquent_by_year(self, tax_year):
        """Search for all delinquent properties in a given tax year"""
        try:
            # Get search page
            response = self.session.get(self.SEARCH_URL)
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Submit search form for delinquent properties
            # This depends on the actual form structure
            
            # Example pattern:
            search_data = {
                'tax_year': tax_year,
                'status': 'delinquent',
                'search_type': 'all'
            }
            
            results = self.session.post(self.SEARCH_URL, data=search_data)
            
            # Parse results
            properties = self._parse_search_results(results.text)
            
            time.sleep(2)  # Rate limiting
            return properties
            
        except Exception as e:
            print(f"Error searching year {tax_year}: {e}")
            return []
    
    def _parse_search_results(self, html):
        """Parse property listings from search results"""
        properties = []
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find property rows (adjust selectors based on actual page)
        rows = soup.find_all('tr', class_='property-row')
        
        for row in rows:
            try:
                prop = {
                    'parcel': self._extract_text(row, '.parcel-number'),
                    'owner': self._extract_text(row, '.owner-name'),
                    'address': self._extract_text(row, '.property-address'),
                    'total_due': self._extract_amount(row, '.total-due'),
                    'delinquent_years': self._extract_text(row, '.delinquent-years')
                }
                properties.append(prop)
            except Exception as e:
                print(f"Error parsing row: {e}")
                continue
        
        return properties
    
    def get_property_detail(self, parcel):
        """Get detailed tax information for a specific parcel"""
        try:
            detail_url = f"{self.BASE_URL}/real-estate-taxes/parcel/{parcel}"
            response = self.session.get(detail_url)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            detail = {
                'parcel': parcel,
                'current_year_due': self._extract_amount(soup, '#current-year-due'),
                'prior_years_due': self._extract_amount(soup, '#prior-years-due'),
                'total_due': self._extract_amount(soup, '#total-due'),
                'certificate_number': self._extract_text(soup, '#certificate-number'),
                'certificate_year': self._extract_text(soup, '#certificate-year')
            }
            
            time.sleep(1)
            return detail
            
        except Exception as e:
            print(f"Error getting detail for {parcel}: {e}")
            return None
    
    def _extract_text(self, soup, selector):
        """Extract text from element"""
        elem = soup.select_one(selector)
        return elem.text.strip() if elem else ''
    
    def _extract_amount(self, soup, selector):
        """Extract dollar amount from element"""
        text = self._extract_text(soup, selector)
        # Remove $ and commas, convert to float
        clean = re.sub(r'[,$]', '', text)
        try:
            return float(clean)
        except:
            return 0.0


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
            # Get search page to grab viewstate tokens
            response = self.session.get(self.SEARCH_URL)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # ASP.NET requires viewstate
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})
            viewstate_val = viewstate['value'] if viewstate else ''
            
            # Submit search
            search_data = {
                '__VIEWSTATE': viewstate_val,
                'ctl00$MainContent$txtParcelID': parcel,
                'ctl00$MainContent$btnSearch': 'Search'
            }
            
            result = self.session.post(self.SEARCH_URL, data=search_data)
            
            # Parse property details
            return self._parse_property_details(result.text)
            
        except Exception as e:
            print(f"Error searching parcel {parcel}: {e}")
            return None
    
    def _parse_property_details(self, html):
        """Parse property details from results page"""
        soup = BeautifulSoup(html, 'html.parser')
        
        try:
            details = {
                'owner_name': self._extract_text(soup, '#MainContent_lblOwnerName'),
                'owner_address': self._extract_text(soup, '#MainContent_lblOwnerAddress'),
                'property_address': self._extract_text(soup, '#MainContent_lblPropertyAddress'),
                'city': self._extract_text(soup, '#MainContent_lblCity'),
                'zip': self._extract_text(soup, '#MainContent_lblZip'),
                'legal_description': self._extract_text(soup, '#MainContent_lblLegal'),
                'assessed_value': self._extract_amount(soup, '#MainContent_lblAssessed'),
                'market_value': self._extract_amount(soup, '#MainContent_lblMarket')
            }
            return details
        except Exception as e:
            print(f"Error parsing property details: {e}")
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


class DuvalTaxDeedNoticeScraper:
    """
    Scrapes Tax Deed Notices from Duval Clerk Official Records
    URL: https://www.duvalclerk.com/real-estate/official-records/search
    """
    
    BASE_URL = "https://www.duvalclerk.com"
    SEARCH_URL = f"{BASE_URL}/real-estate/official-records/search"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def search_recent_notices(self, days_back=90):
        """Search for Tax Deed Notices filed in last N days"""
        try:
            from datetime import timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            # Search for documents containing "Tax Deed"
            search_data = {
                'doc_type': 'NTD',  # Notice of Tax Deed
                'date_start': start_date.strftime('%m/%d/%Y'),
                'date_end': end_date.strftime('%m/%d/%Y')
            }
            
            response = self.session.post(self.SEARCH_URL, data=search_data)
            
            return self._parse_notice_results(response.text)
            
        except Exception as e:
            print(f"Error searching notices: {e}")
            return []
    
    def _parse_notice_results(self, html):
        """Parse notice listings"""
        notices = []
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find document rows
        rows = soup.find_all('tr', class_='doc-row')
        
        for row in rows:
            try:
                notice = {
                    'doc_number': self._extract_text(row, '.doc-number'),
                    'recorded_date': self._extract_text(row, '.recorded-date'),
                    'party_names': self._extract_text(row, '.parties'),
                    'legal_description': self._extract_text(row, '.legal'),
                    'parcel': self._extract_parcel_from_legal(row)
                }
                notices.append(notice)
            except Exception as e:
                print(f"Error parsing notice: {e}")
                continue
        
        return notices
    
    def _extract_parcel_from_legal(self, row):
        """Extract parcel number from legal description"""
        legal = self._extract_text(row, '.legal')
        # Parcel numbers typically follow a pattern like: 123456-7890
        match = re.search(r'\b\d{6}-\d{4}\b', legal)
        return match.group(0) if match else ''
    
    def _extract_text(self, soup, selector):
        """Extract text from element"""
        elem = soup.select_one(selector) if hasattr(soup, 'select_one') else soup.find(class_=selector.strip('.'))
        return elem.text.strip() if elem else ''


class DuvalTaxDeedAuctionScraper:
    """
    Scrapes upcoming Tax Deed Auction listings
    URL: https://www.duvalclerk.com/real-estate/tax-deed-sales
    """
    
    BASE_URL = "https://www.duvalclerk.com"
    AUCTION_URL = f"{BASE_URL}/real-estate/tax-deed-sales"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def get_upcoming_auctions(self):
        """Get all upcoming tax deed auctions"""
        try:
            response = self.session.get(self.AUCTION_URL)
            
            if response.status_code != 200:
                return []
            
            return self._parse_auction_listings(response.text)
            
        except Exception as e:
            print(f"Error getting auctions: {e}")
            return []
    
    def _parse_auction_listings(self, html):
        """Parse auction property listings"""
        auctions = []
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find auction listings
        listings = soup.find_all('div', class_='auction-listing')
        
        for listing in listings:
            try:
                auction = {
                    'parcel': self._extract_text(listing, '.parcel'),
                    'certificate_number': self._extract_text(listing, '.certificate'),
                    'owner': self._extract_text(listing, '.owner'),
                    'address': self._extract_text(listing, '.address'),
                    'assessed_value': self._extract_amount(listing, '.assessed-value'),
                    'opening_bid': self._extract_amount(listing, '.opening-bid'),
                    'auction_date': self._extract_text(listing, '.auction-date'),
                    'status': self._extract_text(listing, '.status')
                }
                auctions.append(auction)
            except Exception as e:
                print(f"Error parsing auction listing: {e}")
                continue
        
        return auctions
    
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