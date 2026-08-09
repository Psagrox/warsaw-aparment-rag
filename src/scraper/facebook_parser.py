import json
import logging
import re
from typing import Any, Dict, Optional
from openai import OpenAI
from src.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """You are a real estate data extraction assistant specializing in Polish apartment listings FOR SALE (SPRZEDAŻ) in Warsaw.
Analyze the provided Facebook group post and extract structured apartment details into a JSON object with the following fields:

- "is_apartment_for_sale": boolean (true ONLY if the post is offering an apartment FOR SALE / SPRZEDAŻ. Return false for rental offers / WYNAMEM / do wynajęcia / najem, roommate searches, buying inquiries, parking spaces, or non-sale posts).
- "title": string (a concise 3-8 word title summarizing the sale offer, e.g. "Sprzedam 2-pokojowe mieszkanie Mokotów"), or null if not a sale offer.
- "price_pln": number or null (total sale price in PLN, e.g. 650000; ignore monthly rent prices).
- "sqm": number or null (area in square meters, e.g. 45.5).
- "rooms": integer or null (number of rooms).
- "district": string or null (Warsaw district name e.g. "Mokotów", "Śródmieście", "Wola", "Ochota", "Ursynów", "Bielany", "Bemowo", "Żoliborz", "Targówek", "Praga-Południe", "Praga-Północ", "Ursus", "Wawer", "Włochy", "Wilanów").

IMPORTANT: If the post is renting an apartment (wynajem, do wynajęcia, najem, kaucja), set "is_apartment_for_sale": false!

Respond ONLY with valid JSON.
"""


def parse_facebook_post_with_llm(post_text: str) -> Optional[Dict[str, Any]]:
    """Extract structured real estate sale attributes from raw Facebook post text using GPT-4o-mini."""
    if not post_text or len(post_text.strip()) < 15:
        return None

    text_lower = post_text.lower()

    # Pre-filter out clear rental offers if no sale keywords are present
    rental_keywords = ["do wynajęcia", "wynajmę", "wynajem", "najem", "odstąpię najem", "opłaty + najem", "kaucja"]
    sale_keywords = ["sprzedam", "na sprzedaż", "sprzedaż", "kupię", "cena sprzedaż"]

    if any(rk in text_lower for rk in rental_keywords) and not any(sk in text_lower for sk in sale_keywords):
        logger.debug("Skipping rental post based on pre-filter keywords.")
        return None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract details from this Facebook post:\n\n{post_text[:2500]}"},
            ],
            temperature=0.0,
            max_tokens=300,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        data = json.loads(content)
        if not data.get("is_apartment_for_sale", False):
            return None

        return data
    except Exception as e:
        logger.warning("Error parsing Facebook post with LLM: %s", str(e))
        return None
