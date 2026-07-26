"""
Scrapers package for Warsaw real estate portals.
"""
from src.scraper.otodom import OtodomScraper
from src.scraper.olx import OlxScraper
from src.scraper.adresowo import AdresowoScraper
from src.scraper.nieruchomosci_online import NieruchomosciOnlineScraper

__all__ = [
    "OtodomScraper",
    "OlxScraper",
    "AdresowoScraper",
    "NieruchomosciOnlineScraper",
]
