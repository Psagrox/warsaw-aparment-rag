import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


class MorizonScraper:
    """Scraper for Morizon.pl real estate apartment offers in Warsaw."""

    def __init__(self) -> None:
        self.base_url: str = "https://www.morizon.pl"
        self.headers: Dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Referer": "https://www.google.com/",
        }
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def _get_with_backoff(self, url: str, retries: int = 3) -> Optional[httpx.Response]:
        """Fetch URL with exponential backoff on HTTP 429 (Rate Limit) or connection errors."""
        for attempt in range(1, retries + 1):
            headers = self.headers.copy()
            headers["User-Agent"] = random.choice(USER_AGENTS)
            try:
                response = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
                if response.status_code == 429:
                    wait_time = random.uniform(3.5, 7.0) * attempt
                    print(f"⚠️ [Morizon] HTTP 429 (Rate Limit). Retrying in {wait_time:.1f}s (Attempt {attempt}/{retries})...")
                    time.sleep(wait_time)
                    continue
                return response
            except Exception as e:
                logger.debug("Attempt %d failed for %s: %s", attempt, url, str(e))
                if attempt < retries:
                    time.sleep(random.uniform(2.0, 4.0))
        return None

    def _parse_rooms(self, raw_rooms: Any) -> Optional[int]:
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
        if not text:
            return None
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for p in reversed(parts):
            if p.lower() not in ["warszawa", "mazowieckie"]:
                return p.title()
        return parts[0] if parts else None

    def _extract_fallback_params(self, text: str, url: str = "") -> Dict[str, Any]:
        """Extract missing sqm, rooms, district, or price from title/URL text."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None, "district": None}
        if not text and not url:
            return res

        combined = f"{text} {url}"

        # Extract sqm e.g. "51m2", "51 m²"
        sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", combined, re.IGNORECASE)
        if sqm_match:
            try:
                res["sqm"] = float(sqm_match.group(1).replace(",", "."))
            except ValueError:
                pass

        # Extract rooms
        if "kawalerka" in combined.lower():
            res["rooms"] = 1
        else:
            rooms_match = re.search(r"(\d+)\s*(?:-| )*(?:pok|pokoj|pokój)", combined, re.IGNORECASE)
            if rooms_match:
                try:
                    res["rooms"] = int(rooms_match.group(1))
                except ValueError:
                    pass

        # Extract price
        price_match = re.search(r"(\d[\d\s\xa0\.]*)\s*(?:zł|PLN)", text, re.IGNORECASE)
        if price_match:
            raw_price = price_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
            try:
                res["price_pln"] = float(raw_price)
            except ValueError:
                pass

        return res

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        if page == 1:
            url = f"{self.base_url}/mieszkania/warszawa/"
        else:
            url = f"{self.base_url}/mieszkania/warszawa/?page={page}"

        response = self._get_with_backoff(url)
        if not response or response.status_code != 200:
            status_str = f"HTTP {response.status_code}" if response else "Connection Failure"
            logger.warning("Morizon search page %d failed (%s)", page, status_str)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        cards = soup.select("div.card__outer") or soup.find_all("div", class_=lambda c: c and "card__outer" in c if c else False)

        for card in cards:
            link = card.select_one("a.property-card[href]") or card.find("a", href=lambda h: h and "/oferta/" in h)
            if not link or not link.get("href"):
                continue

            raw_href = link["href"]
            offer_url = f"{self.base_url}{raw_href}" if raw_href.startswith("/") else raw_href
            clean_url = offer_url.split("?")[0]

            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            # Canonical offer ID
            id_match = (
                re.search(r"-(mzn\d+|mzn-[a-zA-Z0-9]+|\d+)$", clean_url)
                or re.search(r"-(mzn[a-zA-Z0-9]+)$", clean_url)
                or re.search(r"(\d{6,12})", clean_url)
            )
            offer_id = id_match.group(1) if id_match else str(hash(clean_url))[:8]

            title_elem = card.find(attrs={"data-cy": "propertyCardTitle"})
            title = title_elem.get_text(strip=True) if title_elem else "Mieszkanie na sprzedaż (Warszawa)"

            loc_elem = card.find(attrs={"data-cy": "propertyCardLocation"})
            loc_text = loc_elem.get_text(strip=True) if loc_elem else ""

            price_elem = card.find(attrs={"data-cy": "propertyCardPrice"})
            price_txt = price_elem.get_text(strip=True) if price_elem else ""
            p_match = re.search(r"(\d[\d\s\xa0\.]*)\s*zł", price_txt)
            price_val = float(p_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")) if p_match else None

            price_m2_elem = card.find(attrs={"data-cy": "offerPricePerM2"})
            price_m2_txt = price_m2_elem.get_text(strip=True) if price_m2_elem else ""
            pm2_match = re.search(r"(\d[\d\s\xa0\.]*)\s*zł", price_m2_txt)
            price_per_sqm = float(pm2_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")) if pm2_match else None

            area_elem = card.find(attrs={"data-cy": "cardPropertyInfoArea"})
            area_txt = area_elem.get_text(strip=True) if area_elem else ""
            s_match = re.search(r"(\d+(?:[\.,]\d+)?)", area_txt)
            sqm_val = float(s_match.group(1).replace(",", ".")) if s_match else None

            rooms_elem = card.find(attrs={"data-cy": "cardPropertyInfoRooms"})
            rooms_txt = rooms_elem.get_text(strip=True) if rooms_elem else ""
            r_match = re.search(r"(\d+)", rooms_txt)
            rooms_val = int(r_match.group(1)) if r_match else None

            district = self._extract_district(loc_text)

            fallbacks = self._extract_fallback_params(card.get_text(" ", strip=True), clean_url)
            price_val = price_val or fallbacks["price_pln"]
            sqm_val = sqm_val or fallbacks["sqm"]
            rooms_val = rooms_val or fallbacks["rooms"]
            district = district or fallbacks["district"]

            if not price_per_sqm and price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            apartments.append({
                "external_id": f"morizon-{offer_id}",
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
        """Fetch full description from Morizon detail page."""
        response = self._get_with_backoff(url)
        if not response or response.status_code != 200:
            return None

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            desc_div = (
                soup.find("div", class_=lambda c: c and "details-description" in c if c else False)
                or soup.find("div", attrs={"data-cy": "offerDescription"})
                or soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
            )
            return desc_div.get_text(separator="\n", strip=True) if desc_div else None
        except Exception:
            return None

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [Morizon] ({idx}/{total}) Fetching details...")
            apt["description"] = self.get_full_description(url)

            if apt["description"]:
                extra = self._extract_fallback_params(apt["description"], url)
                apt["sqm"] = apt["sqm"] or extra["sqm"]
                apt["rooms"] = apt["rooms"] or extra["rooms"]
                apt["price_pln"] = apt["price_pln"] or extra["price_pln"]
                apt["district"] = apt["district"] or extra["district"]
                if not apt["price_per_sqm"] and apt["price_pln"] and apt["sqm"]:
                    apt["price_per_sqm"] = round(apt["price_pln"] / apt["sqm"], 2)

            time.sleep(random.uniform(1.0, 2.0))
        else:
            apt["description"] = None
        return apt

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        logger.info("Starting Morizon scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [Morizon] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing Morizon offer: %s", str(e))

        logger.info("Morizon scraping complete. Total: %d", len(all_apartments))
        return all_apartments
