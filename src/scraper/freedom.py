import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
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

    # Polish text date e.g. "02 lipca 2026"
    text_match = re.search(r"(\d{1,2})\s+([a-zA-ZzłóśćążęńZŁÓŚĆĄŻĘŃ]+)\s+(\d{4})", raw_clean)
    if text_match:
        day = text_match.group(1).zfill(2)
        month_word = text_match.group(2).lower()
        year = text_match.group(3)
        month_code = MONTH_MAP.get(month_word)
        if month_code:
            return f"{year}-{month_code}-{day}"

    return None


class FreedomScraper:
    """Scraper for Freedom.pl (Freedom Nieruchomości) real estate apartment offers in Warsaw."""

    def __init__(self) -> None:
        self.base_url: str = "https://freedom.pl"
        self.headers: Dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Referer": "https://freedom.pl/",
        }
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def _get_with_backoff(self, url: str, retries: int = 3) -> Optional[httpx.Response]:
        """Fetch URL with exponential backoff on HTTP 429 or connection errors."""
        for attempt in range(1, retries + 1):
            headers = self.headers.copy()
            headers["User-Agent"] = random.choice(USER_AGENTS)
            try:
                response = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
                if response.status_code == 429:
                    wait_time = random.uniform(3.0, 6.0) * attempt
                    print(f"⚠️ [Freedom] HTTP 429 (Rate Limit). Retrying in {wait_time:.1f}s (Attempt {attempt}/{retries})...")
                    time.sleep(wait_time)
                    continue
                return response
            except Exception as e:
                logger.debug("Attempt %d failed for %s: %s", attempt, url, str(e))
                if attempt < retries:
                    time.sleep(random.uniform(1.5, 3.0))
        return None

    def _extract_district(self, text: str) -> Optional[str]:
        if not text:
            return None
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for p in parts:
            p_clean = p.lower()
            if p_clean not in ["warszawa", "mazowieckie"] and not p_clean.startswith("ul."):
                return p.capitalize()
        return parts[0] if parts else None

    def _extract_fallback_params(self, text: str, url: str = "") -> Dict[str, Any]:
        """Extract missing sqm, rooms, district, or price from text/URL."""
        res: Dict[str, Any] = {"sqm": None, "rooms": None, "price_pln": None, "district": None, "date_posted": None}
        if not text and not url:
            return res

        combined = f"{text} {url}"

        # Extract sqm
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

        # Extract date e.g. "Opublikowano: 27.07.2026" or "Dodano 17.07.2026"
        date_match = re.search(r"(?:Opublikowano|Dodano|Aktualizacja)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4})", text, re.IGNORECASE)
        if date_match:
            res["date_posted"] = parse_date_to_iso(date_match.group(1))

        return res

    def get_search_results(self, page: int = 1) -> List[Dict[str, Any]]:
        if page == 1:
            url = f"{self.base_url}/mieszkania/Warszawa/mz/"
        else:
            url = f"{self.base_url}/mieszkania/Warszawa/mz/page/{page}/"

        response = self._get_with_backoff(url)
        if not response or response.status_code != 200:
            status_str = f"HTTP {response.status_code}" if response else "Connection Failure"
            logger.warning("Freedom search page %d failed (%s)", page, status_str)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        apartments: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        cards = soup.select("div.offert") or soup.find_all("div", class_=lambda c: c and "offert" in c if c else False)

        for card in cards:
            link = card.select_one("a.offert-box-ctn-link[href]") or card.find("a", href=lambda h: h and "/oferta/" in h)
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
                re.search(r"-(\d+-\d+-[a-z]+)/?$", clean_url)
                or re.search(r"-(\d+)/?$", clean_url)
                or re.search(r"(\d{5,10})", clean_url)
            )
            offer_id = id_match.group(1) if id_match else str(hash(clean_url))[:8]

            addr_elem = card.find("address")
            addr_txt = addr_elem.get_text(strip=True) if addr_elem else ""
            district = self._extract_district(addr_txt)

            price_elem = card.select_one("div.price strong")
            price_txt = price_elem.get_text(strip=True) if price_elem else ""
            p_match = re.search(r"(\d[\d\s\xa0\.]*)\s*zł", price_txt)
            price_val = float(p_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")) if p_match else None

            pm2_elem = card.select_one("div.price p strong")
            pm2_txt = pm2_elem.get_text(strip=True) if pm2_elem else ""
            pm2_match = re.search(r"(\d[\d\s\xa0\.]*)", pm2_txt)
            price_per_sqm = float(pm2_match.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")) if pm2_match else None

            details_str = " ".join([s.get_text(strip=True) for s in card.select("div.details strong")])
            s_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*m", details_str)
            sqm_val = float(s_match.group(1).replace(",", ".")) if s_match else None

            r_match = re.search(r"(\d+)\s*pok", details_str, re.IGNORECASE)
            rooms_val = int(r_match.group(1)) if r_match else None

            date_elem = card.select_one("div.bottom small")
            date_txt = date_elem.get_text(strip=True) if date_elem else ""
            date_posted = parse_date_to_iso(date_txt)

            fallbacks = self._extract_fallback_params(card.get_text(" ", strip=True), clean_url)
            price_val = price_val or fallbacks["price_pln"]
            sqm_val = sqm_val or fallbacks["sqm"]
            rooms_val = rooms_val or fallbacks["rooms"]
            district = district or fallbacks["district"]
            date_posted = date_posted or fallbacks.get("date_posted")

            if not price_per_sqm and price_val and sqm_val and sqm_val > 0:
                price_per_sqm = round(price_val / sqm_val, 2)

            stored_district = f"{district} - {date_posted}" if date_posted and district else (district or "Warszawa")
            title = f"Mieszkanie Warszawa ({district or 'Warszawa'})"

            apartments.append({
                "external_id": f"freedom-{offer_id}",
                "title": title,
                "price_pln": price_val,
                "price_per_sqm": price_per_sqm,
                "sqm": sqm_val,
                "rooms": rooms_val,
                "district": stored_district,
                "date_posted": date_posted,
                "url": offer_url,
            })

        return apartments

    def get_full_description(self, url: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """Fetch description text and metadata from Freedom.pl detail page."""
        response = self._get_with_backoff(url)
        meta: Dict[str, Any] = {}
        if not response or response.status_code != 200:
            return None, meta

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            meta = self._extract_fallback_params(page_text, url)

            article = soup.find("div", class_="left-page") or soup.find("div", class_="article-page")
            if article:
                ps = article.find_all("p")
                paragraphs = [p.get_text(strip=True) for p in ps if len(p.get_text(strip=True)) > 20]
                if paragraphs:
                    return "\n\n".join(paragraphs), meta
            
            main_desc = soup.find("div", class_=lambda c: c and "description" in c.lower() if c else False)
            desc_text = main_desc.get_text(separator="\n", strip=True) if main_desc else None
            return desc_text, meta
        except Exception:
            return None, meta

    def _fetch_details_worker(self, apt: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        url = apt.get("url", "")
        if url:
            print(f"  [Freedom] ({idx}/{total}) Fetching details...")
            desc_text, meta = self.get_full_description(url)
            apt["description"] = desc_text

            apt["sqm"] = apt["sqm"] or meta.get("sqm")
            apt["rooms"] = apt["rooms"] or meta.get("rooms")
            apt["price_pln"] = apt["price_pln"] or meta.get("price_pln")
            date_posted = apt.get("date_posted") or meta.get("date_posted")

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
        logger.info("Starting Freedom.pl scraper for Warsaw...")
        all_apartments: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            apartments = self.get_search_results(page)
            if not apartments:
                break

            total = len(apartments)
            print(f"  [Freedom] Found {total} offers on page {page}. Fetching details safely...")

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self._fetch_details_worker, apt, i + 1, total)
                    for i, apt in enumerate(apartments)
                ]
                for future in as_completed(futures):
                    try:
                        all_apartments.append(future.result())
                    except Exception as e:
                        logger.error("Error processing Freedom offer: %s", str(e))

        logger.info("Freedom scraping complete. Total: %d", len(all_apartments))
        return all_apartments
