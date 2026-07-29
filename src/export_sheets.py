import logging
import os
import sys
from typing import Any, Dict, List, Optional, Set
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Set stdout encoding for Windows console compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()


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
        worksheet_name: str = "Apartments",
    ) -> None:
        self.sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
        self.credentials_path = credentials_path or os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        self.worksheet_name = worksheet_name

    def export(self, apartments: List[Dict[str, Any]], start_index: int = 1) -> int:
        """Appends new apartments to the Google Sheet.

        Args:
            apartments: List of apartment dictionaries.
            start_index: Starting index number for numbering.

        Returns:
            int: Number of new rows appended.
        """
        if not apartments:
            print("ℹ️ No apartments to export to Google Sheets.")
            return 0

        if not self.sheet_id:
            raise ValueError(
                "Missing GOOGLE_SHEET_ID. Please set it in your .env file or pass --sheet-id <ID>."
            )

        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"Google Service Account credentials file not found at '{self.credentials_path}'. "
                "Please place your Google service account credentials JSON file in the project folder "
                "or specify GOOGLE_CREDENTIALS_FILE in your .env file."
            )

        try:
            import gspread

            client = gspread.service_account(filename=self.credentials_path)
            spreadsheet = client.open_by_key(self.sheet_id)

            try:
                sheet = spreadsheet.worksheet(self.worksheet_name)
            except gspread.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(title=self.worksheet_name, rows=1000, cols=15)

            existing_values = sheet.get_all_values()

            # Ensure header row exists
            if not existing_values:
                sheet.append_row(self.HEADER_ROW)
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

                portal = "Otodom"
                if "olx.pl" in url:
                    portal = "OLX"
                elif "adresowo.pl" in url:
                    portal = "Adresowo"
                elif "nieruchomosci-online.pl" in url:
                    portal = "Nieruchomości-online"
                elif "morizon.pl" in url:
                    portal = "Morizon"

                raw_date = apt.get("created_at") or apt.get("updated_at") or ""
                date_str = "N/A"
                if raw_date:
                    try:
                        date_str = str(raw_date).split("T")[0]
                    except Exception:
                        date_str = str(raw_date)[:10]

                district = apt.get("district") or "N/A"
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
                print("ℹ️ All apartments are already present in the Google Sheet. 0 new rows added.")
                return 0

        except Exception as e:
            print(f"❌ Error exporting to Google Sheets: {e}")
            raise


def export_to_google_sheet(apartments: List[Dict[str, Any]], sheet_id: Optional[str] = None) -> int:
    exporter = GoogleSheetsExporter(sheet_id=sheet_id)
    return exporter.export(apartments)
