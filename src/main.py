import sys
import time
from typing import List, Tuple

# Import config to fail-fast if environment variables are missing
import src.config

from src.db.supabase_client import SupabaseApartmentClient
from src.scraper import (
    AdresowoScraper,
    NieruchomosciOnlineScraper,
    OlxScraper,
    OtodomScraper,
)


def main() -> None:
    print("🚀 Warsaw Apartments RAG ETL Job Starting ...")
    start_time = time.time()

    max_pages: int = 1

    # List of configured scrapers for Warsaw real estate portals
    scrapers: List[Tuple[str, object]] = [
        ("Otodom", OtodomScraper()),
        ("OLX", OlxScraper()),
        ("Adresowo", AdresowoScraper()),
        ("Nieruchomości-online", NieruchomosciOnlineScraper()),
    ]

    all_apartments: List[dict] = []

    # Step 1: Scraping all configured portals
    print("\nSTEP 1: Scraping real estate portals...")
    for name, scraper in scrapers:
        print(f"\n--- Scraping {name} ---")
        try:
            data = scraper.run(max_pages=max_pages)
            print(f"Scraped {len(data)} apartments from {name}")
            all_apartments.extend(data)
        except Exception as e:
            print(f"Error scraping {name}: {e}")

    print(f"\nTotal scraped apartments across all portals: {len(all_apartments)}")

    if not all_apartments:
        print("No apartments found from any portal. Exiting...")
        sys.exit(1)

    # Step 2: Vectorization and upsert to Supabase
    print("\nSTEP 2: Vectorization and upserting into Supabase DB...")
    try:
        db_client = SupabaseApartmentClient()
        db_client.upsert_apartments(all_apartments)
        print("Successfully processed vector embeddings and loaded into Supabase.")
    except Exception as e:
        print(f"Error upserting apartments to Supabase: {e}")
        sys.exit(1)

    end_time = time.time()
    print(f"\n✅ ETL Job completed successfully in {end_time - start_time:.2f} seconds.")


if __name__ == "__main__":
    main()
