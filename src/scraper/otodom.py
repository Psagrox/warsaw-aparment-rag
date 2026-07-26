import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)


class OtodomScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        }
        self.base_url = "https://www.otodom.pl"
        self.timeout = httpx.Timeout(8.0, connect=4.0)

    def _parse_rooms(self, raw_rooms: Any) -> Optional[int]:
        ROOM_MAP = {
            "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
            "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10
        }

        if raw_rooms is None:
            return None
        if isinstance(raw_rooms, int):
            return raw_rooms

        s_rooms = str(raw_rooms).strip().upper()
        if s_rooms in ROOM_MAP:
            return ROOM_MAP[s_rooms]

        try:
            return int(s_rooms)
        except (ValueError, TypeError):
            return None

    def _parse_district(self, location_data: Optional[Dict]) -> Optional[str]:
        if not location_data or not isinstance(location_data, dict):
            return None

        locations = location_data.get("reverseGeocoding", {}).get("locations", [])
        for loc in locations:
            if isinstance(loc, dict) and loc.get("locationLevel") == "district":
                return loc.get("name")

        address = location_data.get("address", {})
        if isinstance(address, dict):
            district = address.get("district")
            if isinstance(district, dict):
                return district.get("name")

        return None

    def get_search_results(self, page: int = 1) -> List[Dict]:
        url = f"{self.base_url}/pl/wyniki/sprzedaz/mieszkanie/mazowieckie/warszawa/warszawa/warszawa?page={page}&market=SECONDARY"

        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("Otodom search error: HTTP %d at page %d", response.status_code, page)
                return []
        except Exception as e:
            logger.error("Error during request to %s: %s", url, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        script_data = soup.find("script", id="__NEXT_DATA__")

        if not script_data or not script_data.string:
            logger.warning("JSON data not found on page %d", page)
            return []

        try:
            json_data = json.loads(script_data.string)
            items = json_data['props']['pageProps']['data']['searchAds']['items']
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            logger.error("Error parsing JSON data: %s", str(e))
            return []

        apartments = []
        for item in items:
            if item.get('slug'):
                apartments.append({
                    "external_id": f"otodom-{item.get('id')}",
                    "title": item.get('title'),
                    "price_pln": item.get('totalPrice', {}).get('value'),
                    "price_per_sqm": item.get('pricePerSquareMeter', {}).get('value'),
                    "sqm": item.get('areaInSquareMeters'),
                    "rooms": self._parse_rooms(item.get('roomsNumber')),
                    "district": self._parse_district(item.get('location')),
                    "url": f"{self.base_url}/pl/oferta/{item.get('slug')}"
                })
        return apartments

    def get_full_description(self, url: str) -> Optional[str]:
        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            desc_div = soup.find("div", {"data-cy": "adPageAdDescription"})
            return desc_div.get_text(separator="\n", strip=True) if desc_div else None
        except Exception:
            return None

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [Otodom] ({idx}/{total}) Fetching details...")
            apt["description"] = self.get_full_description(url)
            time.sleep(random.uniform(0.8, 1.8))
        else:
            apt["description"] = None
        return apt

    def run(self, max_pages: int = 1) -> List[Dict]:
        logger.info("Starting scrape on Otodom for Warsaw...")
        all_apartments = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [Otodom] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing Otodom offer: %s", str(e))

        logger.info("Otodom scraping completed. Total: %d", len(all_apartments))
        return all_apartments


if __name__ == "__main__":
    scraper = OtodomScraper()
    data = scraper.run(max_pages=1)
    print(json.dumps(data, indent=2, ensure_ascii=False))