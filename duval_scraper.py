import requests
from bs4 import BeautifulSoup
import time
import json

class DuvalTaxDeedScraper:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://duval.realtaxdeed.com"
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        
    def handle_disclaimers(self):
        """Handle all disclaimer pages automatically"""
        print("Starting disclaimer acceptance process...")
        
        # Initial page load
        response = self.session.get(f"{self.base_url}/index.cfm")
        print(f"Initial page loaded: {response.status_code}")
        
        # Keep accepting disclaimers until we get through
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            attempts += 1
            
            # Parse the page to look for disclaimer/notice
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if we're still on a disclaimer page
            # Look for notice acceptance button or NID parameter
            notice_script = None
            for script in soup.find_all('script'):
                if script.string and 'NID' in script.string:
                    notice_script = script.string
                    break
            
            # Try to find NID in the page
            nid = self._extract_nid(response.text)
            
            if nid:
                print(f"Found disclaimer NID: {nid}, accepting...")
                accept_response = self.accept_disclaimer(nid)
                
                if accept_response and accept_response.status_code == 200:
                    print(f"Disclaimer {nid} accepted")
                    time.sleep(1)
                    
                    # Reload the page to see if there's another disclaimer
                    response = self.session.get(f"{self.base_url}/index.cfm")
                else:
                    print("Failed to accept disclaimer")
                    break
            else:
                # No more disclaimers found
                print("No more disclaimers found - ready to scrape!")
                return True
        
        print(f"Completed {attempts} disclaimer attempts")
        return True
    
    def _extract_nid(self, html_content):
        """Extract Notice ID from the page"""
        # Look for NID in various places
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Method 1: Look in onclick handlers
        for element in soup.find_all(onclick=True):
            onclick = element.get('onclick', '')
            if 'AcceptNotice' in onclick and 'NID' in onclick:
                # Extract NID from AcceptNotice(NID)
                try:
                    start = onclick.find('(') + 1
                    end = onclick.find(')')
                    nid = onclick[start:end].strip()
                    return nid
                except:
                    pass
        
        # Method 2: Look for hidden input with NID
        nid_input = soup.find('input', {'name': 'NID'})
        if nid_input:
            return nid_input.get('value')
        
        # Method 3: Look in JavaScript
        for script in soup.find_all('script'):
            if script.string and 'NID' in script.string:
                # Try to extract NID value
                import re
                match = re.search(r'NID["\']?\s*[:=]\s*["\']?(\d+)', script.string)
                if match:
                    return match.group(1)
        
        return None
    
    def accept_disclaimer(self, nid):
        """Accept a specific disclaimer by NID"""
        url = f"{self.base_url}/index.cfm"
        
        data = {
            'zaction': 'AJAX',
            'zmethod': 'COM',
            'process': 'NOTICE',
            'func': 'ACCEPT',
            'showjson': 'false',
            'NID': nid
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': '*/*',
            'Origin': self.base_url,
            'Referer': f'{self.base_url}/index.cfm'
        }
        
        response = self.session.post(url, data=data, headers=headers)
        return response
    
    def scrape_listings(self):
        """Scrape the tax deed listings after disclaimers are accepted"""
        print("\nScraping listings...")
        
        # Get the main listings page
        response = self.session.get(f"{self.base_url}/index.cfm")
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Save the HTML for inspection
        with open('duval_page.html', 'w', encoding='utf-8') as f:
            f.write(soup.prettify())
        
        print("Page saved to duval_page.html for inspection")
        
        # Look for auction/property listings
        # This will depend on the actual structure of the page
        # Common patterns:
        listings = []
        
        # Try to find tables with property data
        tables = soup.find_all('table')
        print(f"Found {len(tables)} tables on the page")
        
        for i, table in enumerate(tables):
            print(f"\nTable {i+1}:")
            rows = table.find_all('tr')
            print(f"  Rows: {len(rows)}")
            
            # Print first few rows to see structure
            for j, row in enumerate(rows[:3]):
                cells = row.find_all(['td', 'th'])
                print(f"  Row {j+1}: {len(cells)} cells")
        
        return listings


def main():
    scraper = DuvalTaxDeedScraper()
    
    # Step 1: Handle all disclaimers
    if scraper.handle_disclaimers():
        print("\n" + "="*50)
        print("Disclaimers accepted successfully!")
        print("="*50)
        
        # Step 2: Scrape the actual data
        listings = scraper.scrape_listings()
        
        print(f"\nFound {len(listings)} listings")
    else:
        print("Failed to handle disclaimers")


if __name__ == "__main__":
    main()
