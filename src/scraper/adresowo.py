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


class AdresowoScraper:
    """Scraper for Adresowo.pl real estate apartment offers in Warsaw."""

    def __init__(self) -> None:
        self.base_url: str = "https://adresowo.pl"
        self.headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        }
        self.timeout = httpx.Timeout(10.0, connect=5.0)
        self._last_detail_meta: Dict[str, Any] = {}

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
            p_clean = part.replace("Warszawa", "").strip()
            if p_clean and p_clean.lower() not in ["warszawa", "mazowieckie"]:
                return p_clean.capitalize()
        return parts[0] if parts else None

    def _extract_fallback_params(self, text: str, url: str = "") -> Dict[str, Any]:
        """Extract missing sqm, rooms, district, or price from title/URL text."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None, "district": None}
        if not text and not url:
            return res

        combined = f"{text} {url}"

        # Extract sqm e.g. "55.5 m²", "55,5 m2"
        sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", combined, re.IGNORECASE)
        if sqm_match:
            try:
                res["sqm"] = float(sqm_match.group(1).replace(",", "."))
            except ValueError:
                pass

        # Extract rooms e.g. "3-pokojowe", "3 pok"
        if "kawalerka" in combined.lower():
            res["rooms"] = 1
        else:
            rooms_match = re.search(r"(\d+)\s*(?:-| )*(?:pok|pokoj|pokój)", combined, re.IGNORECASE)
            if rooms_match:
                try:
                    res["rooms"] = int(rooms_match.group(1))
                except ValueError:
                    pass

        # Extract price e.g. "899 000 zł"
        price_match = re.search(r"(\d[\d\s\xa0\.]*)\s*(?:zł|PLN)", text, re.IGNORECASE)
        if price_match:
            raw_price = price_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
            try:
                res["price_pln"] = float(raw_price)
            except ValueError:
                pass

        # Extract district e.g. "warszawa-ochota-ul" or "Warszawa Ochota"
        dist_match = re.search(r"warszawa-([a-zA-Z-żółćęśąźńZÓŁĆĘŚĄŹŃ]+)-ul", url, re.IGNORECASE) or re.search(r"Warszawa\s+([a-zA-Z-żółćęśąźńZÓŁĆĘŚĄŹŃ]+)", text, re.IGNORECASE)
        if dist_match:
            raw_dist = dist_match.group(1).replace("-", " ").strip()
            if raw_dist.lower() not in ["mieszkanie", "sprzedaz"]:
                res["district"] = raw_dist.title()

        return res

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        if page == 1:
            url = f"{self.base_url}/mieszkania/warszawa/"
        else:
            url = f"{self.base_url}/mieszkania/warszawa/_p{page}"

        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                logger.warning("Adresowo search returned HTTP %d", response.status_code)
                return []
        except Exception as e:
            logger.error("Error fetching Adresowo page %d: %s", page, str(e))
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        cards = soup.select("div[data-offer-card]") or soup.select("div.result-item, div.offer-box")

        for card in cards:
            link = card.find("a", href=True)
            if not link:
                continue

            offer_url = link["href"]
            if not offer_url.startswith("http"):
                offer_url = f"{self.base_url}{offer_url}"

            clean_url = offer_url.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            price_val, sqm_val, rooms_val = None, None, None
            p_tags = card.select("div.items-center p") or card.find_all("p")
            for p in p_tags:
                txt = p.get_text(strip=True)
                if "zł" in txt:
                    p_bold = p.find("span", class_=lambda c: c and "font-bold" in c if c else False) or p
                    try:
                        parsed_p = float(p_bold.get_text(strip=True).replace(" ", "").replace("\xa0", "").replace(".", "").replace("zł", ""))
                        if parsed_p > 1000:
                            price_val = parsed_p
                    except (ValueError, TypeError):
                        pass
                elif "m²" in txt or "m2" in txt:
                    s_bold = p.find("span", class_=lambda c: c and "font-bold" in c if c else False) or p
                    try:
                        parsed_s = float(s_bold.get_text(strip=True).replace("m²", "").replace("m2", "").replace(",", ".").strip())
                        if parsed_s > 10:
                            sqm_val = parsed_s
                    except (ValueError, TypeError):
                        pass
                elif "pok" in txt:
                    r_bold = p.find("span", class_=lambda c: c and "font-bold" in c if c else False) or p
                    try:
                        rooms_val = int(re.search(r"(\d+)", r_bold.get_text(strip=True)).group(1))
                    except (ValueError, TypeError, AttributeError):
                        pass

            spans = card.select("h2 a span") or card.select("h3 a span")
            district = None
            street = ""
            if spans:
                loc_text = spans[0].get_text(strip=True)
                if "Warszawa" in loc_text:
                    district = loc_text.replace("Warszawa", "").strip()
                    district = district.title() if district else None
                if len(spans) > 1:
                    street = spans[1].get_text(strip=True)

            fallbacks = self._extract_fallback_params(card.get_text(" ", strip=True), offer_url)
            price_val = price_val or fallbacks["price_pln"]
            sqm_val = sqm_val or fallbacks["sqm"]
            rooms_val = rooms_val or fallbacks["rooms"]
            district = district or fallbacks["district"]

            title = f"Mieszkanie Warszawa {district or ''} {street}".strip()

            price_per_sqm = None
            if price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            id_match = re.search(r"-([a-zA-Z0-9]+)$", clean_url) or re.search(r"(\d+)", clean_url)
            offer_id = id_match.group(1) if id_match else str(hash(clean_url))[:8]

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
        """Fetch description and extract any missing offer metadata from the detail page."""
        self._last_detail_meta = {"price_pln": None, "sqm": None, "rooms": None, "district": None}
        try:
            response = httpx.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            title_tag = soup.title.get_text(strip=True) if soup.title else ""
            self._last_detail_meta = self._extract_fallback_params(f"{title_tag} {page_text}", url)

            desc_container = (
                soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
                or soup.find("section", class_=lambda c: c and "description" in c.lower() if c else False)
                or soup.find("div", {"itemprop": "description"})
            )
            return desc_container.get_text(separator="\n", strip=True) if desc_container else None
        except Exception:
            return None

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [Adresowo] ({idx}/{total}) Fetching details...")
            apt["description"] = self.get_full_description(url)

            meta = getattr(self, "_last_detail_meta", {})
            apt["price_pln"] = apt["price_pln"] or meta.get("price_pln")
            apt["sqm"] = apt["sqm"] or meta.get("sqm")
            apt["rooms"] = apt["rooms"] or meta.get("rooms")
            apt["district"] = apt["district"] or meta.get("district")

            if not apt["price_per_sqm"] and apt["price_pln"] and apt["sqm"]:
                apt["price_per_sqm"] = round(apt["price_pln"] / apt["sqm"], 2)

            time.sleep(random.uniform(0.8, 1.8))
        else:
            apt["description"] = None
        return apt

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        logger.info("Starting Adresowo scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [Adresowo] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing Adresowo offer: %s", str(e))

        logger.info("Adresowo scraping complete. Total: %d", len(all_apartments))
        return all_apartments
