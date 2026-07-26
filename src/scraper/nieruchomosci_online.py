import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)


class NieruchomosciOnlineScraper:
    """Scraper for Nieruchomosci-online.pl (Direct owners / bez pośredników in Warsaw)."""

    def __init__(self) -> None:
        self.base_url: str = "https://www.nieruchomosci-online.pl"
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
        """Convert raw rooms format to an integer."""
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
        """Clean string and parse numeric value."""
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
        for part in parts:
            if part.lower() not in ["warszawa", "mazowieckie"]:
                return part
        return parts[0] if parts else None

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        """Fetch search results for direct owner ('bez pośredników') apartment sales in Warsaw."""
        # URL specifying Warsaw apartments for sale directly from owners (bez-posrednikow)
        if page == 1:
            url = "https://warszawa.nieruchomosci-online.pl/szukaj.html?3,mieszkanie,sprzedaz,,Warszawa&bez-posrednikow=1"
        else:
            url = f"https://warszawa.nieruchomosci-online.pl/szukaj.html?3,mieszkanie,sprzedaz,,Warszawa&bez-posrednikow=1&p={page}"

        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.error("Error fetching Nieruchomosci-online page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []

        # Find offer containers
        cards = (
            soup.find_all("div", class_=lambda c: c and "tile" in c.lower() if c else False)
            or soup.find_all("div", class_=lambda c: c and "offer" in c.lower() if c else False)
            or soup.select("div.primary-title, div[data-id]")
        )

        for card in cards:
            link = card.find("a", href=True)
            if not link:
                continue

            offer_url = link["href"]
            if not offer_url.startswith("http"):
                offer_url = f"{self.base_url}{offer_url}" if offer_url.startswith("/") else f"https://warszawa.nieruchomosci-online.pl/{offer_url}"

            title = link.get_text(strip=True) or (card.find("h2").get_text(strip=True) if card.find("h2") else None)

            price_elem = card.find("span", class_=lambda c: c and "price" in c.lower() if c else False) or card.find("p", class_=lambda c: c and "price" in c.lower() if c else False)
            price_val = self._parse_number(price_elem.get_text(strip=True)) if price_elem else None

            # Params (sqm, price/sqm, rooms)
            info_elem = card.find("div", class_=lambda c: c and "info" in c.lower() if c else False) or card
            info_text = info_elem.get_text() if info_elem else ""

            sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*m²", info_text)
            sqm_val = self._parse_number(sqm_match.group(1)) if sqm_match else None

            price_sqm_match = re.search(r"(\d+(?:\s*\d+)?)\s*zł/m²", info_text)
            price_per_sqm = self._parse_number(price_sqm_match.group(1)) if price_sqm_match else None

            rooms_match = re.search(r"(\d+)\s*pok", info_text, re.IGNORECASE)
            rooms_val = self._parse_rooms(rooms_match.group(1)) if rooms_match else None

            location_elem = card.find("span", class_=lambda c: c and ("location" in c.lower() or "address" in c.lower()) if c else False)
            district = self._extract_district(location_elem.get_text(strip=True)) if location_elem else None

            if not price_per_sqm and price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            # Generate external_id prefixed with "no-"
            offer_id = card.get("data-id") or card.get("id")
            if not offer_id:
                id_match = re.search(r",(\d+)\.html", offer_url) or re.search(r"-(\d+)\.html", offer_url)
                offer_id = id_match.group(1) if id_match else str(hash(offer_url))[:8]

            apartments.append({
                "external_id": f"no-{offer_id}",
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
        """Fetch details page and extract full description text.

        Returns None if request fails (HTTP 403/404 or network error).
        """
        try:
            response = httpx.get(url, headers=self.headers, timeout=15.0, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("Nieruchomosci-online request failed with HTTP %d for URL: %s", response.status_code, url)
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            desc_div = (
                soup.find("div", class_=lambda c: c and "box-description" in c if c else False)
                or soup.find("div", class_=lambda c: c and "offer-description" in c if c else False)
                or soup.find("div", id="description")
                or soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
            )

            if desc_div:
                return desc_div.get_text(separator="\n", strip=True)
            return None

        except httpx.RequestError as e:
            logger.error("HTTP request error for %s: %s", url, str(e))
            return None

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        """Orchestrate scraper execution over max_pages fetching full descriptions."""
        logger.info("Starting Nieruchomosci-online scraper for Warsaw (Direct owner filter)...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            logger.info("Fetching Nieruchomosci-online search page %d...", page)
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

        logger.info("Nieruchomosci-online scraping complete. Retrieved %d offers.", len(all_apartments))
        return all_apartments


if __name__ == "__main__":
    scraper = NieruchomosciOnlineScraper()
    data = scraper.run(max_pages=1)
    print(json.dumps(data, indent=2, ensure_ascii=False))
