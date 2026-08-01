import logging
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Set stdout encoding for Windows console compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

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


def parse_date_to_iso(raw: Optional[str]) -> str:
    """Converts raw date strings or timestamps into clean YYYY-MM-DD format."""
    if not raw or raw == "N/A":
        return "N/A"

    raw_clean = str(raw).strip()

    # Polish text date e.g. "02 lipca 2026"
    text_match = re.search(r"(\d{1,2})\s+([a-zA-ZzłóśćążęńZŁÓŚĆĄŻĘŃ]+)\s+(\d{4})", raw_clean)
    if text_match:
        day = text_match.group(1).zfill(2)
        month_word = text_match.group(2).lower()
        year = text_match.group(3)
        month_code = MONTH_MAP.get(month_word)
        if month_code:
            return f"{year}-{month_code}-{day}"

    # DD.MM.YYYY
    dot_match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw_clean)
    if dot_match:
        d, m, y = dot_match.group(1).zfill(2), dot_match.group(2).zfill(2), dot_match.group(3)
        return f"{y}-{m}-{d}"

    # ISO format YYYY-MM-DD
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw_clean)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

    # "Dzisiaj" / "Today"
    if "dzisiaj" in raw_clean.lower() or "today" in raw_clean.lower():
        return datetime.now().strftime("%Y-%m-%d")

    # "Wczoraj" / "Yesterday"
    if "wczoraj" in raw_clean.lower() or "yesterday" in raw_clean.lower():
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    return raw_clean[:10]


def extract_clean_district_and_date(apt: Dict[str, Any]) -> Tuple[str, str]:
    """Extracts clean district name and parsed YYYY-MM-DD post date."""
    district_raw = apt.get("district") or "N/A"
    raw_date = apt.get("date_posted") or apt.get("date") or ""

    clean_district = district_raw
    if "-" in district_raw:
        parts = district_raw.split("-", 1)
        if re.search(r"(\d{1,2}\s+[a-zA-ZzłóśćążęńZŁÓŚĆĄŻĘŃ]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4})", parts[1]):
            clean_district = parts[0].strip()
            if not raw_date or raw_date == "N/A":
                raw_date = parts[1].strip()

    if not raw_date or raw_date == "N/A":
        raw_date = apt.get("created_at") or apt.get("updated_at") or ""

    parsed_date = parse_date_to_iso(raw_date)
    return clean_district, parsed_date


class GoogleSheetsExporter:
    """Exporter to append apartment search results to a Google Sheet without duplicates."""

    HEADER_ROW = [
        "#",
        "Date Posted",
        "Portal",
        "District",
        "Price (PLN)",
        "Area (m²)",
        "Rooms",
        "Price / m²",
        "Details",
        "Direct Link",
    ]

    def __init__(
        self,
        sheet_id: Optional[str] = None,
        credentials_path: Optional[str] = None,
        worksheet_name: Optional[str] = None,
    ) -> None:
        self.sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        self.credentials_path = credentials_path or os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        self.worksheet_name = worksheet_name

    def export(self, apartments: List[Dict[str, Any]], start_index: int = 1) -> int:
        """Appends new apartments to the Google Sheet (Sheet1 / main tab by default)."""
        if not apartments:
            print("ℹ️ No apartments to export to Google Sheets.")
            return 0

        if not self.sheet_id:
            print("\n⚠️ [Google Sheets Export Notice]")
            print("   GOOGLE_SHEET_ID is missing.")
            print("   👉 Please set GOOGLE_SHEET_ID=your_sheet_id in your .env file or pass --sheet-id <ID>.\n")
            return 0

        if not os.path.exists(self.credentials_path):
            abs_path = os.path.abspath(self.credentials_path)
            print("\n⚠️ [Google Sheets Credentials Required]")
            print(f"   Credentials file not found at: '{abs_path}'")
            print("   👉 To fix this:")
            print("   1. Download your Service Account JSON key from Google Cloud Console.")
            print(f"   2. Save it as '{self.credentials_path}' in your project root directory.")
            print("   3. Share your Google Sheet with the service account email (with Editor permission).\n")
            return 0

        try:
            import gspread

            client = gspread.service_account(filename=self.credentials_path)
            spreadsheet = client.open_by_key(self.sheet_id)

            # Target primary tab (sheet1) unless a custom worksheet name is specified
            if self.worksheet_name:
                try:
                    sheet = spreadsheet.worksheet(self.worksheet_name)
                except gspread.WorksheetNotFound:
                    sheet = spreadsheet.add_worksheet(title=self.worksheet_name, rows=1000, cols=15)
            else:
                sheet = spreadsheet.sheet1

            existing_values = sheet.get_all_values()

            # Check if sheet is empty or lacks header row
            is_empty_or_no_header = (
                not existing_values
                or not any(cell.strip() for row in existing_values for cell in row)
                or (existing_values and existing_values[0] and existing_values[0][0] != "#")
            )

            if is_empty_or_no_header:
                sheet.clear()
                sheet.append_row(self.HEADER_ROW, value_input_option="USER_ENTERED")
                existing_values = [self.HEADER_ROW]

            # Collect existing URLs or offer IDs from sheet to avoid duplicate rows
            existing_urls: Set[str] = set()
            for row in existing_values:
                for cell in row:
                    if "http" in cell:
                        existing_urls.add(cell.strip().lower())

            rows_to_append: List[List[Any]] = []

            current_row_idx = len(existing_values) if existing_values else 1

            for apt in apartments:
                url = apt.get("url") or ""
                clean_url = url.strip().lower()

                if clean_url and clean_url in existing_urls:
                    continue  # Skip duplicate row

                if clean_url:
                    existing_urls.add(clean_url)

                ext_id = (apt.get("external_id") or "").lower()
                portal = "Otodom"
                if "olx.pl" in clean_url or ext_id.startswith("olx-"):
                    portal = "OLX"
                elif "adresowo.pl" in clean_url or ext_id.startswith("adresowo-"):
                    portal = "Adresowo"
                elif "nieruchomosci-online.pl" in clean_url or ext_id.startswith("no-"):
                    portal = "Nieruchomości-online"
                elif "morizon.pl" in clean_url or ext_id.startswith("morizon-"):
                    portal = "Morizon"
                elif "freedom.pl" in clean_url or ext_id.startswith("freedom-"):
                    portal = "Freedom"

                district, date_str = extract_clean_district_and_date(apt)

                price_val = apt.get("price_pln")
                sqm_val = apt.get("sqm")
                price_sqm_val = apt.get("price_per_sqm")

                if not price_sqm_val and price_val and sqm_val and sqm_val > 0:
                    price_sqm_val = round(price_val / sqm_val, 2)

                desc = apt.get("description") or ""
                green_keywords = ["park", "las", "zielon", "drzewa", "skwer", "spacer", "balkon", "metro"]
                highlight_snippet = "No details snippet"
                if desc:
                    sentences = [s.strip() for s in desc.split(".") if s.strip()]
                    for sentence in sentences:
                        if any(kw in sentence.lower() for kw in green_keywords):
                            highlight_snippet = sentence[:65] + ("..." if len(sentence) > 65 else "")
                            break
                    if highlight_snippet == "No details snippet" and sentences:
                        highlight_snippet = sentences[0][:65] + ("..." if len(sentences[0]) > 65 else "")

                row = [
                    current_row_idx,
                    date_str,
                    portal,
                    district,
                    price_val if price_val else "N/A",
                    sqm_val if sqm_val else "N/A",
                    apt.get("rooms") or "N/A",
                    price_sqm_val if price_sqm_val else "N/A",
                    highlight_snippet,
                    url,
                ]

                rows_to_append.append(row)
                current_row_idx += 1

            if rows_to_append:
                sheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                print(f"✅ Appended {len(rows_to_append)} new rows to Google Sheet '{spreadsheet.title}' (Worksheet: '{sheet.title}').")
                return len(rows_to_append)
            else:
                print(f"ℹ️ All apartments are already present in Google Sheet '{spreadsheet.title}' ({sheet.title}). 0 new rows added.")
                return 0

        except Exception as e:
            print(f"❌ Error exporting to Google Sheets: {e}")
            return 0


def export_to_google_sheet(apartments: List[Dict[str, Any]], sheet_id: Optional[str] = None) -> int:
    exporter = GoogleSheetsExporter(sheet_id=sheet_id)
    return exporter.export(apartments)
