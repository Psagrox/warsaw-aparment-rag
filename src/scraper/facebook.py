import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from src.config import APIFY_API_TOKEN
from src.scraper.facebook_parser import parse_facebook_post_with_llm

try:
    from apify_client import ApifyClient

    HAS_APIFY = True
except ImportError:
    ApifyClient = None
    HAS_APIFY = False

logger = logging.getLogger(__name__)

import os

# Default Warsaw real estate selling (SPRZEDAŻ) Facebook Groups
DEFAULT_GROUP_URLS = [
    "https://www.facebook.com/groups/mieszkania.na.sprzedaz.warszawa/",
    "https://www.facebook.com/groups/mieszkaniawarszawasprzedam/",
    "https://www.facebook.com/groups/837545249614246/",
    "https://www.facebook.com/groups/633977609988127/",
    "https://www.facebook.com/groups/1692547524296119/",
]



class FacebookScraper:
    """Scraper for Warsaw Real Estate Facebook Groups using Apify API & OpenAI Post Parsing."""

    def __init__(self, group_urls: Optional[List[str]] = None) -> None:
        if group_urls:
            self.group_urls = group_urls
        else:
            env_urls = os.getenv("FB_GROUP_URLS", "").strip()
            if env_urls:
                self.group_urls = [u.strip() for u in env_urls.split(",") if u.strip()]
            else:
                self.group_urls = DEFAULT_GROUP_URLS
        self.api_token = APIFY_API_TOKEN

    def run(self, max_pages: int = 1) -> List[Dict[str, Any]]:
        """Fetch posts from target Facebook Groups via Apify and parse with LLM.

        Args:
            max_pages: Multiplier for maximum posts to scrape (e.g. 1 page ~ 15 posts per group).
        """
        if not self.api_token:
            print(
                "⚠️ [Facebook Scraper] APIFY_API_TOKEN is missing or empty in your environment / .env file."
            )
            print(
                "💡 Please obtain a free token from https://console.apify.com/account/integrations and add APIFY_API_TOKEN=... to .env"
            )
            return []

        if not HAS_APIFY:
            print(
                "⚠️ [Facebook Scraper] 'apify-client' module is not installed. Run: pip install apify-client"
            )
            return []

        logger.info(
            "Starting Facebook Groups scraper via Apify for %d groups...",
            len(self.group_urls),
        )
        print(
            f"  [Facebook] Invoking Apify actor for {len(self.group_urls)} Warsaw groups..."
        )

        client = ApifyClient(self.api_token)
        max_posts = max_pages * 15

        run_input = {
            "startUrls": [{"url": u} for u in self.group_urls],
            "resultsLimit": max_posts,
        }

        apartments: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            # Run the Facebook Groups Scraper actor on Apify
            run = client.actor("apify/facebook-groups-scraper").call(run_input=run_input)
            dataset_id = getattr(run, "default_dataset_id", None) or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
            if not dataset_id:
                print("❌ [Facebook] Could not retrieve dataset ID from Apify run.")
                return []

            dataset_items = list(client.dataset(dataset_id).iterate_items())
            print(f"  [Facebook] Fetched {len(dataset_items)} raw posts from Apify. Parsing offers with GPT-4o-mini...")

            for item in dataset_items:
                post_text = (
                    item.get("text")
                    or item.get("message")
                    or item.get("postText")
                    or ""
                )
                if not post_text or len(post_text.strip()) < 20:
                    continue

                post_url = item.get("url") or item.get("postUrl") or ""
                post_id = (
                    item.get("id")
                    or item.get("postId")
                    or str(hash(post_url or post_text[:50]))[:10]
                )

                ext_id = f"fb-{post_id}"
                if ext_id in seen_ids:
                    continue
                seen_ids.add(ext_id)

                # Extract post date
                raw_time = item.get("time") or item.get("timestamp") or item.get("date")
                date_posted = None
                if raw_time:
                    try:
                        if isinstance(raw_time, (int, float)):
                            date_posted = datetime.fromtimestamp(raw_time).strftime(
                                "%Y-%m-%d"
                            )
                        else:
                            date_posted = str(raw_time)[:10]
                    except Exception:
                        pass
                if not date_posted:
                    date_posted = datetime.now().strftime("%Y-%m-%d")

                # Extract structured real estate data using OpenAI LLM
                parsed = parse_facebook_post_with_llm(post_text)
                if not parsed:
                    continue  # Not a real estate sale/rent offer or parse failed

                title = parsed.get("title") or "Mieszkanie z Grupy FB Warszawa"
                price_val = parsed.get("price_pln")
                sqm_val = parsed.get("sqm")
                rooms_val = parsed.get("rooms")
                district_name = parsed.get("district") or "Warszawa"

                stored_district = (
                    f"{district_name} - {date_posted}"
                    if date_posted and district_name
                    else district_name
                )

                price_per_sqm = None
                if price_val and sqm_val and sqm_val > 0:
                    price_per_sqm = round(price_val / sqm_val, 2)

                apartments.append(
                    {
                        "external_id": ext_id,
                        "title": f"[FB] {title}",
                        "price_pln": price_val,
                        "price_per_sqm": price_per_sqm,
                        "sqm": sqm_val,
                        "rooms": rooms_val,
                        "district": stored_district,
                        "date_posted": date_posted,
                        "description": post_text,
                        "url": post_url or f"https://www.facebook.com/groups/{post_id}",
                    }
                )

        except Exception as e:
            logger.error("Error executing Facebook Groups Apify scraper: %s", str(e))
            print(f"❌ [Facebook] Error calling Apify actor: {e}")

        logger.info(
            "Facebook Scraper finished. Extracted %d parsed apartment offers.",
            len(apartments),
        )
        return apartments
