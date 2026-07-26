import time
import sys

#Import config so we make ure that it will fail quick if env vars are missing.
import src.config

from src.scraper.otodom import OtodomScraper
from src.db.supabase_client import SupabaseApartmentClient


def main():
    print(" Warsaw Apartments ETL Job Starting ...")
    start_time = time.time()

    #Step 1
    print("\nSTEP 1: Scraping Otodom for apartments...")
    scraper = OtodomScraper()
    #First test we use only 1 page and in production we will increase it
    apartments_data = scraper.run(max_pages=1)
    print(f"Scraped {len(apartments_data)} apartments")

    if not apartments_data:
        print("No apartments found. Exiting...")
        sys.exit(1)



    #Step 2: Vectorization and load in the DB
    print("\nSTEP 2: Vectorization and load in the DB...")
    try:
        db_client = SupabaseApartmentClient()
        db_client.upsert_apartments(apartments_data)
    except Exception as e:
        print(f"Error upserting apartments to Supabase: {e}")
        sys.exit(1)

    end_time = time.time()
    print(f"\nJob completed in {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
    
    
    

    

