import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
]

MONTH_MAP = {
    "stycznia": "01", "stycznie": "01", "styczeń": "01", "sty": "01",
    "lutego": "02", "luty": "02", "lut": "02",
    "marca": "03", "marzec": "03", "mar": "03",
    "kwietnia": "04", "kwiecień": "04", "kwi": "04",
    "maja": "05", "maj": "05",
    "czerwca": "06", "czerwiec": "06", "cze": "06",
    "lipca": "07", "lipiec": "07", "lip": "07",
    "sierpnia": "08", "sierpień": "08", "sie": "08",
    "września": "09", "wrzesień": "09", "wrz": "09",
    "października": "10", "październik": "10", "paź": "10",
    "listopada": "11", "listopad": "11", "lis": "11",
    "grudnia": "12", "grudzień": "12", "gru": "12",
}


def parse_date_to_iso(raw: str) -> Optional[str]:
    """Converts various date strings into YYYY-MM-DD format."""
    if not raw or raw == "N/A":
        return None

    raw_clean = raw.strip()

    # ISO format YYYY-MM-DD
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw_clean)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

    # DD.MM.YYYY
    dot_match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw_clean)
    if dot_match:
        d, m, y = dot_match.group(1).zfill(2), dot_match.group(2).zfill(2), dot_match.group(3)
        return f"{y}-{m}-{d}"

    # "Dzisiaj" / "Today"
    if "dzisiaj" in raw_clean.lower() or "today" in raw_clean.lower():
        return datetime.now().strftime("%Y-%m-%d")

    # "Wczoraj" / "Yesterday"
    if "wczoraj" in raw_clean.lower() or "yesterday" in raw_clean.lower():
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Polish text date e.g. "28 lipca 2026"
    text_match = re.search(r"(\d{1,2})\s+([a-zA-ZzłóśćążęńZŁÓŚĆĄŻĘŃ]+)(?:\s+(\d{4}))?", raw_clean)
    if text_match:
        day = text_match.group(1).zfill(2)
        month_word = text_match.group(2).lower()
        year = text_match.group(3) or str(datetime.now().year)
        month_code = MONTH_MAP.get(month_word)
        if month_code:
            return f"{year}-{month_code}-{day}"

    return None


class OlxScraper:
    """Scraper for OLX.pl real estate apartment offers in Warsaw (Direct owners only)."""

    def __init__(self) -> None:
        self.base_url: str = "https://www.olx.pl"
        self.headers: Dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Referer": "https://www.google.com/",
        }

    def _get_with_backoff(self, url: str, retries: int = 3) -> Optional[Any]:
        """Fetch URL using curl_cffi (impersonate chrome) or httpx fallback."""
        if HAS_CURL_CFFI:
            for attempt in range(1, retries + 1):
                try:
                    res = curl_requests.get(url, impersonate="chrome", timeout=12)
                    if res.status_code == 200:
                        return res
                    if res.status_code == 429:
                        time.sleep(random.uniform(2.0, 4.0) * attempt)
                except Exception as e:
                    logger.debug("curl_cffi attempt %d failed for %s: %s", attempt, url, str(e))
                    time.sleep(1.0)
            return None

        # httpx fallback
        for attempt in range(1, retries + 1):
            headers = self.headers.copy()
            headers["User-Agent"] = random.choice(USER_AGENTS)
            try:
                res = httpx.get(url, headers=headers, timeout=8.0, follow_redirects=True)
                if res.status_code == 200:
                    return res
                if res.status_code == 429:
                    time.sleep(random.uniform(2.0, 4.0) * attempt)
            except Exception as e:
                logger.debug("httpx attempt %d failed for %s: %s", attempt, url, str(e))
                time.sleep(1.0)
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
        parts = [p.strip() for p in text.split(",")]
        if len(parts) > 1:
            for part in parts:
                if part.lower() != "warszawa":
                    return part.title()
        return parts[0].title() if parts else None

    def _extract_fallback_params(self, text: str) -> Dict[str, Any]:
        """Extract missing sqm, rooms, price, or post date from text."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None, "date_posted": None}
        if not text:
            return res

        # Extract sqm e.g. "46.4 m2"
        sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", text, re.IGNORECASE)
        if sqm_match:
            try:
                res["sqm"] = float(sqm_match.group(1).replace(",", "."))
            except ValueError:
                pass

        # Extract rooms e.g. "3-pok", "kawalerka"
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

        # Extract date e.g. "Dodane 28 lipca 2026" or "Dzisiaj o 14:30"
        date_match = re.search(r"(?:Dodane|Odświeżono|Dodano)\s*:?\s*(\d{1,2}\s+[a-zA-ZzłóśćążęńZŁÓŚĆĄŻĘŃ]+(?:\s+\d{4})?|\d{1,2}\.\d{1,2}\.\d{4}|dzisiaj|wczoraj)", text, re.IGNORECASE)
        if date_match:
            res["date_posted"] = parse_date_to_iso(date_match.group(1))

        return res

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/nieruchomosci/mieszkania/sprzedaz/warszawa/?page={page}&search[private_business]=private"

        response = self._get_with_backoff(url)
        if not response or response.status_code != 200:
            logger.warning("OLX search returned HTTP %s", response.status_code if response else "Error")
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

                        raw_created = ad.get("created_time") or ad.get("last_refresh_time") or ""
                        date_posted = parse_date_to_iso(str(raw_created))

                        fallbacks = self._extract_fallback_params(f"{title_str} {offer_url}")
                        sqm_val = sqm_val or fallbacks["sqm"]
                        rooms_val = rooms_val or fallbacks["rooms"]
                        price_val = price_val or fallbacks["price_pln"]
                        date_posted = date_posted or fallbacks.get("date_posted")

                        price_per_sqm = None
                        if price_val and sqm_val and sqm_val > 0:
                            price_per_sqm = round(price_val / sqm_val, 2)

                        stored_district = f"{district_name} - {date_posted}" if date_posted and district_name else (district_name or "Warszawa")

                        apartments.append({
                            "external_id": f"olx-{ad_id}",
                            "title": title_str,
                            "price_pln": price_val,
                            "price_per_sqm": price_per_sqm,
                            "sqm": sqm_val,
                            "rooms": rooms_val,
                            "district": stored_district,
                            "date_posted": date_posted,
                            "url": offer_url,
                        })
                    if apartments:
                        return apartments
            except Exception as e:
                logger.debug("Failed parsing NEXT_DATA JSON from OLX: %s", str(e))

        cards = soup.select("div[data-cy='l-card']") or soup.select("a[href*='/d/oferta/']")

        for card in cards:
            link = card.find("a", href=True) if card.name != "a" else card
            if not link or not link.get("href"):
                continue

            offer_url = link["href"]
            if not offer_url.startswith("http"):
                offer_url = f"{self.base_url}{offer_url}"
            clean_url = offer_url.split("?")[0]

            title_elem = card.find("h6") or card.find("h3") or card.find("strong") or card.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else None

            price_elem = card.find("p", {"data-testid": "ad-price"}) or card.find("p", class_=lambda c: c and "price" in c.lower() if c else False)
            price_val = self._parse_number(price_elem.get_text(strip=True)) if price_elem else None

            id_match = re.search(r"-ID([a-zA-Z0-9]{4,20})(?:\.html)?", clean_url) or re.search(r"ID([a-zA-Z0-9]{4,20})", clean_url)
            ad_id = id_match.group(1) if id_match else str(hash(clean_url))[:8]

            location_elem = card.find("p", {"data-testid": "location-date"})
            location_text = location_elem.get_text(strip=True) if location_elem else ""
            district = self._extract_district(location_text)

            card_full_text = card.get_text(" ", strip=True) + " " + clean_url
            fallbacks = self._extract_fallback_params(card_full_text)

            price_val = price_val or fallbacks["price_pln"]
            sqm_val = fallbacks["sqm"]
            rooms_val = fallbacks["rooms"]
            date_posted = fallbacks.get("date_posted")

            if not title:
                title = f"Mieszkanie na sprzedaż ({district or 'Warszawa'})"

            price_per_sqm = None
            if price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            stored_district = f"{district} - {date_posted}" if date_posted and district else (district or "Warszawa")

            apartments.append({
                "external_id": f"olx-{ad_id}",
                "title": title,
                "price_pln": price_val,
                "price_per_sqm": price_per_sqm,
                "sqm": sqm_val,
                "rooms": rooms_val,
                "district": stored_district,
                "date_posted": date_posted,
                "url": clean_url,
            })

        return apartments

    def get_full_description(self, url: str) -> Optional[str]:
        response = self._get_with_backoff(url)
        if not response or response.status_code != 200:
            return None

        try:
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

            if apt["description"]:
                extra = self._extract_fallback_params(apt["description"])
                apt["sqm"] = apt["sqm"] or extra["sqm"]
                apt["rooms"] = apt["rooms"] or extra["rooms"]
                apt["price_pln"] = apt["price_pln"] or extra["price_pln"]
                date_posted = apt.get("date_posted") or extra.get("date_posted")

                district_raw = apt.get("district") or "Warszawa"
                if date_posted and "-" not in district_raw:
                    apt["district"] = f"{district_raw} - {date_posted}"
                apt["date_posted"] = date_posted

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
