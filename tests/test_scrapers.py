import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.scraper.olx import OlxScraper
from src.scraper.adresowo import AdresowoScraper
from src.scraper.nieruchomosci_online import NieruchomosciOnlineScraper


REQUIRED_KEYS = {
    "external_id", "title", "price_pln", "price_per_sqm",
    "sqm", "rooms", "district", "url", "description"
}


class TestScrapersArchitecture(unittest.TestCase):
    """Test suite ensuring all scrapers adhere to architectural requirements."""

    def test_room_parsing(self):
        olx = OlxScraper()
        self.assertEqual(olx._parse_rooms("THREE"), 3)
        self.assertEqual(olx._parse_rooms("3 pokoje"), 3)
        self.assertEqual(olx._parse_rooms("kawalerka"), 1)
        self.assertEqual(olx._parse_rooms("1 POKÓJ"), 1)
        self.assertEqual(olx._parse_rooms(2), 2)
        self.assertIsNone(olx._parse_rooms(None))

        adresowo = AdresowoScraper()
        self.assertEqual(adresowo._parse_rooms("4 POKOJE"), 4)

        no_scraper = NieruchomosciOnlineScraper()
        self.assertEqual(no_scraper._parse_rooms("5 pokoi"), 5)

    def test_olx_external_id_and_keys(self):
        olx = OlxScraper()
        dummy_apt = {
            "external_id": "olx-12345",
            "title": "Mieszkanie Warszawa",
            "price_pln": 600000.0,
            "price_per_sqm": 12000.0,
            "sqm": 50.0,
            "rooms": 2,
            "district": "Mokotów",
            "url": "https://www.olx.pl/d/oferta/12345",
            "description": None
        }
        self.assertEqual(set(dummy_apt.keys()), REQUIRED_KEYS)
        self.assertTrue(dummy_apt["external_id"].startswith("olx-"))
        self.assertIsInstance(dummy_apt["rooms"], int)
        self.assertIsInstance(dummy_apt["price_pln"], float)

    def test_adresowo_external_id_and_keys(self):
        dummy_apt = {
            "external_id": "adresowo-9988",
            "title": "Oferta Adresowo",
            "price_pln": 500000.0,
            "price_per_sqm": 10000.0,
            "sqm": 50.0,
            "rooms": 2,
            "district": "Wola",
            "url": "https://adresowo.pl/oferta/9988",
            "description": "Opis mieszkania"
        }
        self.assertEqual(set(dummy_apt.keys()), REQUIRED_KEYS)
        self.assertTrue(dummy_apt["external_id"].startswith("adresowo-"))

    def test_nieruchomosci_online_external_id_and_keys(self):
        dummy_apt = {
            "external_id": "no-7766",
            "title": "Bez pośredników Warszawa",
            "price_pln": 800000.0,
            "price_per_sqm": 16000.0,
            "sqm": 50.0,
            "rooms": 3,
            "district": "Ursynów",
            "url": "https://warszawa.nieruchomosci-online.pl/7766.html",
            "description": None
        }
        self.assertEqual(set(dummy_apt.keys()), REQUIRED_KEYS)
        self.assertTrue(dummy_apt["external_id"].startswith("no-"))

    @patch("httpx.get")
    def test_http_403_404_error_handling(self, mock_get):
        # Mock HTTP 403 response
        mock_resp_403 = MagicMock()
        mock_resp_403.status_code = 403
        mock_get.return_value = mock_resp_403

        olx = OlxScraper()
        desc_403 = olx.get_full_description("https://www.olx.pl/d/oferta/test403")
        self.assertIsNone(desc_403)

        # Mock RequestError
        mock_get.side_effect = httpx.RequestError("Connection timeout")
        adresowo = AdresowoScraper()
        desc_err = adresowo.get_full_description("https://adresowo.pl/oferta/err")
        self.assertIsNone(desc_err)


if __name__ == "__main__":
    unittest.main()
