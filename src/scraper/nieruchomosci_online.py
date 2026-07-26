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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        }
        self.timeout = httpx.Timeout(8.0, connect=4.0)

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
        parts = [p.strip() for p in text.split(",")]
        for part in parts:
            if part.lower() not in ["warszawa", "mazowieckie"]:
                return part
        return parts[0] if parts else None

    def _extract_fallback_params(self, text: str) -> Dict[str, Any]:
        """Extract missing sqm, rooms, or price from title/URL text."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None}
        if not text:
            return res

        sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", text, re.IGNORECASE)
        if sqm_match:
            try:
                res["sqm"] = float(sqm_match.group(1).replace(",", "."))
            except ValueError:
                pass

        if "kawalerka" in text.lower():
            res["rooms"] = 1
        else:
            rooms_match = re.search(r"(\d+)\s*(?:-| )*(?:pok|pokoj|pokój)", text, re.IGNORECASE)
            if rooms_match:
                try:
                    res["rooms"] = int(rooms_match.group(1))
                except ValueError:
                    pass

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
            url = "https://warszawa.nieruchomosci-online.pl/szukaj.html?3,mieszkanie,sprzedaz,,Warszawa&bez-posrednikow=1"
        else:
            url = f"https://warszawa.nieruchomosci-online.pl/szukaj.html?3,mieszkanie,sprzedaz,,Warszawa&bez-posrednikow=1&p={page}"

        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("Nieruchomosci-online search returned HTTP %d", response.status_code)
                return []
        except Exception as e:
            logger.error("Error fetching Nieruchomosci-online search page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []

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

            # Fallback extraction from card full text and offer URL
            card_full_text = card.get_text(" ", strip=True) + " " + offer_url
            fallbacks = self._extract_fallback_params(card_full_text)

            price_val = price_val or fallbacks["price_pln"]
            sqm_val = sqm_val or fallbacks["sqm"]
            rooms_val = rooms_val or fallbacks["rooms"]

            if not price_per_sqm and price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            offer_id = card.get("data-id") or card.get("id")
            if not offer_id:
                id_match = re.search(r",(\d+)\.html", offer_url) or re.search(r"-(\d+)\.html", offer_url)
                offer_id = id_match.group(1) if id_match else str(hash(offer_url))[:8]

            if not title:
                title = f"Mieszkanie na sprzedaż ({district or 'Warszawa'})"

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
        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            desc_div = (
                soup.find("div", class_=lambda c: c and "box-description" in c if c else False)
                or soup.find("div", class_=lambda c: c and "offer-description" in c if c else False)
                or soup.find("div", id="description")
                or soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
            )
            return desc_div.get_text(separator="\n", strip=True) if desc_div else None
        except Exception:
            return None

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [Nieruchomości-online] ({idx}/{total}) Fetching details...")
            apt["description"] = self.get_full_description(url)

            if apt["description"]:
                extra = self._extract_fallback_params(apt["description"])
                apt["sqm"] = apt["sqm"] or extra["sqm"]
                apt["rooms"] = apt["rooms"] or extra["rooms"]
                apt["price_pln"] = apt["price_pln"] or extra["price_pln"]
                if not apt["price_per_sqm"] and apt["price_pln"] and apt["sqm"]:
                    apt["price_per_sqm"] = round(apt["price_pln"] / apt["sqm"], 2)

            time.sleep(random.uniform(0.8, 1.8))
        else:
            apt["description"] = None
        return apt

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        logger.info("Starting Nieruchomosci-online scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [Nieruchomości-online] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing Nieruchomosci-online offer: %s", str(e))

        logger.info("Nieruchomosci-online scraping complete. Total: %d", len(all_apartments))
        return all_apartments
