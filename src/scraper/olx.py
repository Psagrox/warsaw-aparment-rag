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
        if len(parts) > 1:
            for part in parts:
                if part.lower() != "warszawa":
                    return part
        return parts[0] if parts else None

    def _extract_fallback_params(self, text: str) -> Dict[str, Any]:
        """Extract missing sqm, rooms, or price from title/URL text."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None}
        if not text:
            return res

        # Extract sqm e.g. "46.4 m2", "46,4m2"
        sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", text, re.IGNORECASE)
        if sqm_match:
            try:
                res["sqm"] = float(sqm_match.group(1).replace(",", "."))
            except ValueError:
                pass

        # Extract rooms e.g. "3-pok", "2 pok", "kawalerka"
        if "kawalerka" in text.lower():
            res["rooms"] = 1
        else:
            rooms_match = re.search(r"(\d+)\s*(?:-| )*(?:pok|pokoj|pokój)", text, re.IGNORECASE)
            if rooms_match:
                try:
                    res["rooms"] = int(rooms_match.group(1))
                except ValueError:
                    pass

        # Extract price e.g. "750 000 zł"
        price_match = re.search(r"(\d[\d\s\xa0\.]*)\s*(?:zł|PLN)", text, re.IGNORECASE)
        if price_match:
            raw_price = price_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
            try:
                res["price_pln"] = float(raw_price)
            except ValueError:
                pass

        return res

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/nieruchomosci/mieszkania/sprzedaz/warszawa/?page={page}&search[private_business]=private"

        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("OLX search returned HTTP %d", response.status_code)
                return []
        except Exception as e:
            logger.error("Error fetching OLX search page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []

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
                        rooms_val = self._parse_rooms(ad.get("params", {}).get("rooms", {}).get("value"))

                        district_name = self._extract_district(ad.get("location", {}).get("city_name", ""))
                        title_str = ad.get("title") or f"Mieszkanie na sprzedaż ({district_name or 'Warszawa'})"
                        offer_url = ad.get("url") if ad.get("url", "").startswith("http") else f"{self.base_url}{ad.get('url', '')}"

                        # Fallback parsing from title/url text if fields are missing
                        fallbacks = self._extract_fallback_params(f"{title_str} {offer_url}")
                        sqm_val = sqm_val or fallbacks["sqm"]
                        rooms_val = rooms_val or fallbacks["rooms"]
                        price_val = price_val or fallbacks["price_pln"]

                        price_per_sqm = None
                        if price_val and sqm_val and sqm_val > 0:
                            price_per_sqm = round(price_val / sqm_val, 2)

                        apartments.append({
                            "external_id": f"olx-{ad_id}",
                            "title": title_str,
                            "price_pln": price_val,
                            "price_per_sqm": price_per_sqm,
                            "sqm": sqm_val,
                            "rooms": rooms_val,
                            "district": district_name,
                            "url": offer_url,
                        })
                    if apartments:
                        return apartments
            except Exception as e:
                logger.debug("Failed parsing NEXT_DATA JSON from OLX: %s", str(e))

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

            title_elem = card.find("h6") or card.find("h3") or card.find("strong") or card.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else None

            price_elem = card.find("p", {"data-testid": "ad-price"}) or card.find("p", class_=lambda c: c and "price" in c.lower() if c else False)
            price_val = self._parse_number(price_elem.get_text(strip=True)) if price_elem else None

            id_match = re.search(r"ID([a-zA-Z0-9]+)", offer_url) or re.search(r"-ID(\d+)", offer_url)
            ad_id = id_match.group(1) if id_match else str(hash(offer_url))[:8]

            location_elem = card.find("p", {"data-testid": "location-date"})
            location_text = location_elem.get_text(strip=True) if location_elem else ""
            district = self._extract_district(location_text)

            card_full_text = card.get_text(" ", strip=True) + " " + offer_url
            fallbacks = self._extract_fallback_params(card_full_text)

            price_val = price_val or fallbacks["price_pln"]
            sqm_val = fallbacks["sqm"]
            rooms_val = fallbacks["rooms"]

            if not title:
                title = f"Mieszkanie na sprzedaż ({district or 'Warszawa'})"

            price_per_sqm = None
            if price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            apartments.append({
                "external_id": f"olx-{ad_id}",
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
                soup.find("div", {"data-cy": "ad_description"})
                or soup.find("div", {"data-testid": "ad_description"})
                or soup.find("div", class_=lambda c: c and "css-1m8mzw3" in c if c else False)
            )
            return desc_div.get_text(separator="\n", strip=True) if desc_div else None
        except Exception:
            return None

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [OLX] ({idx}/{total}) Fetching details...")
            apt["description"] = self.get_full_description(url)

            # Extra parameter extraction from description text if sqm or rooms were missing
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
        logger.info("Starting OLX scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [OLX] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing OLX offer: %s", str(e))

        logger.info("OLX scraping complete. Total: %d", len(all_apartments))
        return all_apartments
