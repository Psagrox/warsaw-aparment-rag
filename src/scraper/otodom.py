import httpx
import json
import time
import random
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

class OtodomScraper:
    def __init__(self):
        #Headers to simulate real navigation
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        }
        self.base_url = "https://www.otodom.pl"

    def get_search_results(self, page: int = 1) -> List[Dict]:
        """Obtains metadata from the aparments"""

        #URL already filter
        url = f"{self.base_url}/pl/wyniki/sprzedaz/mieszkanie/mazowieckie/warszawa/warszawa/warszawa?page={page}&market=SECONDARY"

        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0)
            response.raise_for_status()
        except httpx.RequestException as e:
            print(f"Error during request to {url}: {e}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        script_data = soup.find("script",id="__NEXT_DATA__")

        if not script_data:
            print(f"Error: JSON data not found on page {page}")
            return []

        json_data = json.loads(script_data.string)

        #Navigate through the JSON to find the list of offers.
        #The exact path depends on the current structure of the website
        #Usually it is under 'props' -> 'pageProps' -> 'data' -> 'ads'
        try:
            items = json_data['props']['pageProps']['data']['searchAds']['items']
        except (KeyError, TypeError) as e:
            print(f"Error parsing JSON data: {e}")
            return []

        apartments = []
        for item in items:
                #Otodom sometimes include ads of investment, we skip them
                if item.get('slug'):
                    apartments.append({
                        "external_id": f"otodom-{item.get('id')}",
                        "title": item.get('title'),
                        "price_pln": item.get('totalPrice', {}).get('value'),
                        "price_per_sqm": item.get('pricePerSquareMeter', {}).get('value'),
                        "sqm": item.get('areaInSquareMeters'),
                        "rooms": item.get('roomsNumber'),
                        "district": item.get('location', {}).get('address', {}).get('district', {}).get('name'),
                        "url": f"{self.base_url}/pl/oferta/{item.get('slug')}"
                    })
        return apartments
        
    def get_full_description(self, apartment_url: str) -> Optional[Dict]:
        """Fetches detailed data about a specific apartment from its detail page"""
        try:
            response = httpx.get(apartment_url, headers=self.headers, timeout=15.0)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            #Extrract the container of the description by its atribute data-cy
            desc_div = soup.find("div", attrs={"data-cy": "adPageAdDescription"})
            if desc_div:
                # get_text with separator keeps the paragraphs readable for the embedding
                return desc_div.get_text(separator="\n", strip=True)
            return None

        except httpx.RequestException as e:
            print(f"Error during request to {apartment_url}: {e}")
            return None


    def run(self, max_pages: int = 1) -> List[Dict]:
        """Orquestate extraccion iterating over pages and fetching full details."""
        print(f"Starting scrape on Otodom for Warsaw...")
        all_apartments = []

        for page in range(1, max_pages + 1):
            print(f"Fetching page {page}...")
            apartments = self.get_search_results(page)
            if not apartments:
                print(f"No data found on page {page}, stopping...")
                break

            # For each apartment, fetch the full description
            for apt in apartments:
                print(f"Extracting details: {apt['url'].split('/')[-1]}")
                description = self.get_full_description(apt['url'])
                apt['description'] = description
                all_apartments.append(apt)

                # Politeness delay to avoid being banned from the server
                time.sleep(random.uniform(1.5, 3.5))


        print(f"Scraping completed. Total apartments: {len(all_apartments)}")
        return all_apartments


if __name__ == "__main__":
    scraper = OtodomScraper()
    data = scraper.run(max_pages=1)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Total apartments: {len(data)}") 
        