import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Import config to fail-fast if environment variables are missing
import src.config

from src.db.supabase_client import SupabaseApartmentClient
from src.scraper import (
    AdresowoScraper,
    FacebookScraper,
    FreedomScraper,
    MorizonScraper,
    NieruchomosciOnlineScraper,
    OlxScraper,
    OtodomScraper,
)


def _scrape_portal(name: str, scraper: Any, max_pages: int) -> Tuple[str, List[Dict[str, Any]]]:
    """Execute a single portal scraper in an isolated thread."""
    print(f"🚀 [Thread-Start] Scraping {name} (max_pages={max_pages})...")
    try:
        data = scraper.run(max_pages=max_pages)
        print(f"✅ [Thread-Complete] {name}: Scraped {len(data)} apartments.")
        return name, data
    except Exception as e:
        print(f"❌ [Thread-Error] Error scraping {name}: {e}")
        return name, []


def main() -> None:
    parser = argparse.ArgumentParser(description="Warsaw Apartments RAG ETL Pipeline")
    parser.add_argument(
        "--pages",
        "-p",
        type=int,
        default=1,
        help="Number of search result pages to scrape per portal (default: 1)"
    )
    parser.add_argument(
        "--exclude-portal",
        "--exclude",
        type=str,
        nargs="+",
        help="Exclude specific portal scraper(s) e.g. --exclude morizon freedom facebook"
    )
    parser.add_argument(
        "--portals",
        type=str,
        nargs="+",
        help="Only search specific portal(s) e.g. --portals otodom freedom facebook"
    )
    args, _ = parser.parse_known_args()

    max_pages: int = max(1, args.pages)

    # Master list of configured scrapers
    scrapers: List[Tuple[str, Any]] = [
        ("Otodom", OtodomScraper()),
        ("OLX", OlxScraper()),
        ("Adresowo", AdresowoScraper()),
        ("Nieruchomości-online", NieruchomosciOnlineScraper()),
        ("Morizon", MorizonScraper()),
        ("Freedom", FreedomScraper()),
        ("Facebook", FacebookScraper()),
    ]

    # Apply portal inclusion filter if requested
    if args.portals:
        inc_list = [inc.lower().replace("_", "-") for inc in args.portals]
        scrapers = [
            (name, scraper) for name, scraper in scrapers
            if any(
                inc in name.lower()
                or (inc == "no" and "nieruchomości" in name.lower())
                or (inc == "nieruchomosci-online" and "nieruchomości" in name.lower())
                for inc in inc_list
            )
        ]

    # Apply exclusion filter if requested
    if args.exclude_portal:
        ex_list = [ex.lower().replace("_", "-") for ex in args.exclude_portal]
        scrapers = [
            (name, scraper) for name, scraper in scrapers
            if not any(
                ex in name.lower()
                or (ex == "no" and "nieruchomości" in name.lower())
                or (ex == "nieruchomosci-online" and "nieruchomości" in name.lower())
                for ex in ex_list
            )
        ]

    print(f"🚀 Warsaw Apartments RAG ETL Job Starting ({len(scrapers)} active scrapers, {max_pages} pages per portal) ...")
    start_time = time.time()

    all_apartments: List[Dict[str, Any]] = []

    # Step 1: Scraping selected portals concurrently
    print(f"\nSTEP 1: Concurrently scraping {len(scrapers)} real estate portals...")

    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        future_to_portal = {
            executor.submit(_scrape_portal, name, scraper, max_pages): name
            for name, scraper in scrapers
        }

        for future in as_completed(future_to_portal):
            portal_name = future_to_portal[future]
            try:
                name, data = future.result()
                all_apartments.extend(data)
            except Exception as e:
                print(f"❌ Unexpected thread execution failure for {portal_name}: {e}")

    # Deduplicate scraped apartments by external_id
    unique_scraped: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for apt in all_apartments:
        ext_id = apt.get("external_id")
        if ext_id and ext_id not in seen_ids:
            seen_ids.add(ext_id)
            unique_scraped.append(apt)

    all_apartments = unique_scraped
    total_scraped: int = len(all_apartments)
    print(f"\nTotal unique scraped apartments across active portals: {total_scraped}")

    if not all_apartments:
        print("No apartments found from selected portals. Exiting...")
        sys.exit(0)

    # Step 2: Delta Load check against Supabase
    print("\nSTEP 2: Checking existing records in DB (Delta Load)...")
    try:
        db_client = SupabaseApartmentClient()
        all_external_ids: List[str] = [
            apt["external_id"] for apt in all_apartments if apt.get("external_id")
        ]
        existing_ids: Set[str] = db_client.get_existing_ids(all_external_ids)
    except Exception as e:
        print(f"Error querying existing IDs from Supabase: {e}")
        sys.exit(1)

    # Filter out apartments already present in the database (O(1) set lookup)
    new_apartments: List[Dict[str, Any]] = [
        apt for apt in all_apartments if apt.get("external_id") not in existing_ids
    ]

    ignored_count: int = len(all_apartments) - len(new_apartments)
    new_count: int = len(new_apartments)

    print("\n--- DELTA LOAD SUMMARY ---")
    print(f"📊 Total scraped: {total_scraped}")
    print(f"⏭️  Already existing (skipped): {ignored_count}")
    print(f"✨ New to vectorize and store: {new_count}")

    # Exit early if there are no new apartments to process
    if not new_apartments:
        end_time = time.time()
        print(f"\n🎉 No new apartments to process. Finishing pipeline without consuming OpenAI tokens.")
        print(f"✅ ETL Job completed in {end_time - start_time:.2f} seconds.")
        sys.exit(0)

    # Step 3: Vectorization and upsert of NEW apartments ONLY
    print(f"\nSTEP 3: Vectorizing {new_count} new descriptions with OpenAI and upserting into Supabase...")
    try:
        db_client.upsert_apartments(new_apartments)
        print("Successfully processed vector embeddings and loaded new records into Supabase.")
    except Exception as e:
        print(f"Error upserting new apartments to Supabase: {e}")
        sys.exit(1)

    end_time = time.time()
    print(f"\n✅ ETL Job completed successfully in {end_time - start_time:.2f} seconds.")


if __name__ == "__main__":
    main()
