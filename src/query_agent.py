# src/query_agent.py
import os
from typing import Any, Dict, List
from dotenv import load_dotenv
from supabase import Client, create_client
from openai import OpenAI

load_dotenv()

if not all([os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"), os.getenv("OPENAI_API_KEY")]):
    raise ValueError("Missing environment variables. Please check your .env file.")

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def search_ideal_apartments() -> List[Dict[str, Any]]:
    """Generates semantic embedding query, queries Supabase RPC with hard filters,
    and applies negative keyword filtering.
    """
    # 1. SEMANTIC PROMPT (In Polish to maximize matching with local offer descriptions)
    # Here we inject Tier 1 locations, green areas, nice-to-haves, and "direct owner" instruction
    semantic_query = """
    Sprzedaż bezpośrednia, bez pośredników, bez prowizji (bezpośrednio od właściciela). 
    Lokalizacja: Bielany (Wrzeciono, Marymont, Młociny), Żoliborz, Ursus (Szamoty, Niedźwiadek), lub Bemowo.
    Mieszkanie z rynku wtórnego. Bardzo blisko parków, lasu (Las Bielański, EKOpark) - max 10 minut spacerem.
    Posiada balkon, loggię lub ogródek. Oddzielna widna kuchnia. 
    Stan: gotowe do wprowadzenia, po remoncie. Cicha okolica, blisko stacji metra (M1/M2).
    """

    print("🧠 Generating search vector based on your strict requirements...")
    query_vector = openai_client.embeddings.create(
        input=semantic_query,
        model="text-embedding-3-small"
    ).data[0].embedding

    # 2. HARD FILTERS IN SUPABASE (SQL)
    print("📡 Querying Supabase (Applying Hard Caps: Max 750k PLN, Min 2 Rooms, Min 30m²)...")

    # Invoke RPC function created in Phase 1
    response = supabase.rpc(
        "buscar_apartamentos",
        {
            "query_embedding": query_vector,
            "match_threshold": 0.20,  # Slightly broader initial net for Python filtering
            "match_count": 25,        # Fetch top 25 records to process
            "p_max_price": 750000,    # HARD CAP
            "p_min_sqm": 30,          # HARD CAP
            "p_min_rooms": 2          # HARD CAP
        }
    ).execute()

    raw_results = response.data or []
    filtered_results = []

    # 3. POST-PROCESSING & NEGATIVE FILTERS (Anti-Agency / Anti-Developer)
    for apt in raw_results:
        desc_lower = apt.get('description', '').lower() if apt.get('description') else ""

        # Strict exclusion of primary market (developers)
        if "od dewelopera" in desc_lower or "stan deweloperski" in desc_lower:
            continue

        # Exclusion of agencies/brokers
        if "agencja nieruchomości" in desc_lower or "pobieramy prowizję" in desc_lower:
            continue

        filtered_results.append(apt)

    # Return top 5 results after filtering
    return filtered_results[:5]


def generate_markdown_table(apartments: List[Dict[str, Any]]) -> str:
    """Formats apartment search results into a clean Markdown table."""
    if not apartments:
        return "No apartments were found that strictly meet all requirements at this time."

    md = "| Location / Micro-market | Price (PLN) | Area (m²) | Rooms | Proximity to Green Infrastructure | Verified Direct Link |\n"
    md += "|---|---|---|---|---|---|\n"

    for apt in apartments:
        district = apt.get('district', 'N/A')
        price_val = apt.get('price_pln', 0)
        price = f"{price_val:,.0f}".replace(",", " ") if price_val else "N/A"
        area = f"{apt.get('sqm', 'N/A')}"
        rooms = apt.get('rooms', 'N/A')
        url = apt.get('url', '#')

        # Extract green infrastructure snippet from description
        desc = apt.get('description', '') or ''
        green_keywords = ["park", "las", "zielon", "drzewa", "skwer", "spacer"]
        green_snippet = "Not mentioned"
        for phrase in desc.split('.'):
            if any(kw in phrase.lower() for kw in green_keywords):
                green_snippet = phrase.strip()[:60] + "..."  # Truncate for table
                break

        md += f"| {district} | {price} | {area} | {rooms} | {green_snippet} | [View Offer]({url}) |\n"

    return md


if __name__ == "__main__":
    top_apts = search_ideal_apartments()
    print("\n" + generate_markdown_table(top_apts))