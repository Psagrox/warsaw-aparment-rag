import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)


class OlxScraper:
    """Scraper for OLX.pl real estate apartment offers in Warsaw (Direct owners only)."""

    def __init__(self) -> None:
        self.base_url: str = "https://www.olx.pl"
        self.headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        }

    def _parse_rooms(self, raw_rooms: Any) -> Optional[int]:
        """Convert raw rooms data to integer, supporting word and string formats."""
        if raw_rooms is None:
            return None

        ROOM_MAP: Dict[str, int] = {
            "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
            "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
            "KAWALERKA": 1, "1 POKÓJ": 1, "2 POKOJE": 2, "3 POKOJE": 3,
            "4 POKOJE": 4, "5 POKOI": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
        }

        if isinstance(raw_rooms, int):
            return raw_rooms
        if isinstance(raw_rooms, float):
            return int(raw_rooms)

        s_rooms = str(raw_rooms).strip().upper()
        if s_rooms in ROOM_MAP:
            return ROOM_MAP[s_rooms]

        match = re.search(r"(\d+)", s_rooms)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, TypeError):
                pass
        return None

    def _parse_number(self, val: Any) -> Optional[float]:
        """Clean string and parse float value (e.g. '750 000 zł' or '52,5 m²')."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)

        cleaned = str(val).replace("\xa0", "").replace(" ", "").replace(",", ".")
        match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                return None
        return None

    def _extract_district(self, text: str) -> Optional[str]:
        """Extract Warsaw district name from location string."""
        if not text:
            return None
        parts = [p.strip() for p in text.split(",")]
        if len(parts) > 1:
            for part in parts:
                if part.lower() != "warszawa":
                    return part
        return parts[0] if parts else None

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch search results for private business apartment offers in Warsaw."""
        url = f"{self.base_url}/nieruchomosci/mieszkania/sprzedaz/warszawa/?page={page}&search[private_business]=private"

        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.error("Error fetching OLX search page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []

        # Attempt to parse via NEXT_DATA JSON script tag if available
        script_data = soup.find("script", id="__NEXT_DATA__")
        if script_data and script_data.string:
            try:
                json_data = json.loads(script_data.string)
                ad_items = json_data.get("props", {}).get("pageProps", {}).get("data", {}).get("ads", [])
                if ad_items:
                    for ad in ad_items:
                        ad_id = ad.get("id") or ad.get("id_string")
                        if not ad_id:
                            continue
                        price_val = self._parse_number(ad.get("params", {}).get("price", {}).get("value"))
                        sqm_val = self._parse_number(ad.get("params", {}).get("m", {}).get("value"))
                        price_per_sqm = None
                        if price_val and sqm_val and sqm_val > 0:
                            price_per_sqm = round(price_val / sqm_val, 2)

                        apartments.append({
                            "external_id": f"olx-{ad_id}",
                            "title": ad.get("title"),
                            "price_pln": price_val,
                            "price_per_sqm": price_per_sqm,
                            "sqm": sqm_val,
                            "rooms": self._parse_rooms(ad.get("params", {}).get("rooms", {}).get("value")),
                            "district": self._extract_district(ad.get("location", {}).get("city_name", "")),
                            "url": ad.get("url") if ad.get("url", "").startswith("http") else f"{self.base_url}{ad.get('url', '')}",
                        })
                    if apartments:
                        return apartments
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.debug("Failed parsing NEXT_DATA JSON from OLX: %s", str(e))

        # Fallback HTML scraping
        cards = soup.find_all("div", {"data-cy": "l-card"}) or soup.find_all("div", {"data-testid": "listing-grid"})
        if not cards:
            cards = soup.select("a[href*='/d/oferta/']")

        for card in cards:
            link = card.find("a", href=True) if card.name != "a" else card
            if not link:
                continue

            offer_url = link["href"]
            if not offer_url.startswith("http"):
                offer_url = f"{self.base_url}{offer_url}"

            title_elem = card.find("h6") or card.find("h3") or card.find("strong")
            title = title_elem.get_text(strip=True) if title_elem else None

            price_elem = card.find("p", {"data-testid": "ad-price"}) or card.find("p", class_=lambda c: c and "price" in c.lower() if c else False)
            price_val = self._parse_number(price_elem.get_text(strip=True)) if price_elem else None

            # Extract ad ID from card or URL
            id_match = re.search(r"ID([a-zA-Z0-9]+)", offer_url) or re.search(r"-ID(\d+)", offer_url)
            ad_id = id_match.group(1) if id_match else str(hash(offer_url))[:8]

            location_elem = card.find("p", {"data-testid": "location-date"})
            location_text = location_elem.get_text(strip=True) if location_elem else ""

            apartments.append({
                "external_id": f"olx-{ad_id}",
                "title": title,
                "price_pln": price_val,
                "price_per_sqm": None,
                "sqm": None,
                "rooms": None,
                "district": self._extract_district(location_text),
                "url": offer_url,
            })

        return apartments

    def get_full_description(self, url: str) -> Optional[str]:
        """Fetch the offer page and extract the full description text.

        Returns None if request fails (HTTP 403/404 or RequestError).
        """
        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("OLX request failed with HTTP %d for URL: %s", response.status_code, url)
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            desc_div = (
                soup.find("div", {"data-cy": "ad_description"})
                or soup.find("div", {"data-testid": "ad_description"})
                or soup.find("div", class_=lambda c: c and "css-1m8mzw3" in c if c else False)
            )

            if desc_div:
                return desc_div.get_text(separator="\n", strip=True)
            return None

        except httpx.RequestError as e:
            logger.error("HTTP request error for %s: %s", url, str(e))
            return None

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        """Orchestrate extraction over max_pages fetching full descriptions."""
        logger.info("Starting OLX scraper for Warsaw (Private Business filter)...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            logger.info("Fetching OLX search page %d...", page)
            apartments = self.get_search_results(page)

            if not apartments:
                logger.info("No offers found on page %d. Ending scrape.", page)
                break

            for apt in apartments:
                logger.info("Fetching details for: %s", apt["url"])
                description = self.get_full_description(apt["url"])
                apt["description"] = description
                all_apartments.append(apt)

                # Rate limiting delay
                time.sleep(random.uniform(1.5, 3.5))

        logger.info("OLX scraping complete. Retrieved %d offers.", len(all_apartments))
        return all_apartments


if __name__ == "__main__":
    scraper = OlxScraper()
    data = scraper.run(max_pages=1)
    print(json.dumps(data, indent=2, ensure_ascii=False))
