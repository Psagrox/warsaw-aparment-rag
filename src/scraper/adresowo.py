import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)


class AdresowoScraper:
    """Scraper for Adresowo.pl real estate apartment offers in Warsaw."""

    def __init__(self) -> None:
        self.base_url: str = "https://adresowo.pl"
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
        """Convert raw rooms input to an integer."""
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
        """Parse float from string or numeric values."""
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
        """Extract district name from Adresowo location text."""
        if not text:
            return None
        parts = [p.strip() for p in text.split(",")]
        for part in parts:
            if part.lower() != "warszawa" and part.lower() != "mazowieckie":
                return part
        return parts[0] if parts else None

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch search results for Warsaw apartments from Adresowo.pl."""
        if page == 1:
            url = f"{self.base_url}/mieszkania/warszawa/"
        else:
            url = f"{self.base_url}/mieszkania/warszawa/_p{page}"

        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.error("Error fetching Adresowo page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []

        # Offer item cards on Adresowo
        offer_cards = (
            soup.find_all("div", class_=lambda c: c and "offer-item" in c if c else False)
            or soup.find_all("article", class_=lambda c: c and "offer" in c if c else False)
            or soup.select("div.result-item, div.offer-box, div[data-id]")
        )

        for card in offer_cards:
            link = card.find("a", href=True)
            if not link:
                continue

            offer_url = link["href"]
            if not offer_url.startswith("http"):
                offer_url = f"{self.base_url}{offer_url}"

            title_elem = card.find("h2") or card.find("h3") or card.find("a", class_=lambda c: c and "title" in c if c else False)
            title = title_elem.get_text(strip=True) if title_elem else None

            # Extract price and parameters
            price_elem = card.find("span", class_=lambda c: c and "price" in c if c else False) or card.find("div", class_=lambda c: c and "price" in c if c else False)
            price_val = self._parse_number(price_elem.get_text(strip=True)) if price_elem else None

            price_sqm_elem = card.find("span", class_=lambda c: c and "price-m2" in c if c else False)
            price_per_sqm = self._parse_number(price_sqm_elem.get_text(strip=True)) if price_sqm_elem else None

            sqm_elem = card.find("span", class_=lambda c: c and ("area" in c or "m2" in c or "sqm" in c) if c else False)
            sqm_val = self._parse_number(sqm_elem.get_text(strip=True)) if sqm_elem else None

            rooms_elem = card.find("span", class_=lambda c: c and ("room" in c or "pokoj" in c) if c else False)
            rooms_val = self._parse_rooms(rooms_elem.get_text(strip=True)) if rooms_elem else None

            location_elem = card.find("span", class_=lambda c: c and ("location" in c or "address" in c) if c else False)
            district = self._extract_district(location_elem.get_text(strip=True)) if location_elem else None

            # Compute fallback price_per_sqm if missing
            if not price_per_sqm and price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            # Generate external_id from data-id attribute or URL pattern
            offer_id = card.get("data-id") or card.get("id")
            if not offer_id:
                id_match = re.search(r"(\d+)", offer_url.split("/")[-1])
                offer_id = id_match.group(1) if id_match else str(hash(offer_url))[:8]

            apartments.append({
                "external_id": f"adresowo-{offer_id}",
                "title": title,
                "price_pln": price_val,
                "price_per_sqm": price_per_sqm,
                "sqm": sqm_val,
                "rooms": rooms_val,
                "district": district,
                "url": offer_url,
            })

        return apartments

    def get_full_description(self, url: str) -> Optional[str]:
        """Fetch details page and extract description text.

        Returns None if request fails (HTTP 403/404 or network error).
        """
        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("Adresowo request failed with HTTP %d for URL: %s", response.status_code, url)
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            desc_container = (
                soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
                or soup.find("section", class_=lambda c: c and "description" in c.lower() if c else False)
                or soup.find("div", {"itemprop": "description"})
            )

            if desc_container:
                return desc_container.get_text(separator="\n", strip=True)
            return None

        except httpx.RequestError as e:
            logger.error("HTTP request error for %s: %s", url, str(e))
            return None

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        """Orchestrate scrape iteration over max_pages fetching details and descriptions."""
        logger.info("Starting Adresowo scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            logger.info("Fetching Adresowo search page %d...", page)
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

        logger.info("Adresowo scraping complete. Retrieved %d offers.", len(all_apartments))
        return all_apartments


if __name__ == "__main__":
    scraper = AdresowoScraper()
    data = scraper.run(max_pages=1)
    print(json.dumps(data, indent=2, ensure_ascii=False))
