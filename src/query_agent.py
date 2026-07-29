# src/query_agent.py
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from openai import OpenAI
from supabase import Client, create_client

# Set stdout encoding for Windows console compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load environment variables
load_dotenv()

# Disk cache directory
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "query_agent_cache.json")


def get_supabase_client() -> Client:
    """Initialize and return Supabase client."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment.")
    return create_client(url, key)


def get_openai_client() -> OpenAI:
    """Initialize and return OpenAI client."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Missing OPENAI_API_KEY in environment.")
    return OpenAI(api_key=api_key)


DEFAULT_QUERY_TEXT = """
Sprzedaż bezpośrednia, bez pośredników, bez prowizji (bezpośrednio od właściciela). 
Lokalizacja: Bielany (Wrzeciono, Marymont, Młociny), Żoliborz, Ursus (Szamoty, Niedźwiadek), Bemowo, Mokotów, Wola, Praga lub Śródmieście.
UWAGA: Zdecydowanie wyklucz oferty z dzielnic Białołęka oraz Rembertów.
Mieszkanie z rynku wtórnego. Bardzo blisko parków, lasu (Las Bielański, EKOpark) - max 10 minut spacerem.
Posiada balkon, loggię lub ogródek. Oddzielna widna kuchnia. 
Stan: gotowe do wprowadzenia, po remoncie. Cicha okolica, blisko stacji metra (M1/M2).
"""

DEFAULT_EXCLUDED_DISTRICTS = ["białołęka", "bialoleka", "rembertów", "rembertow"]


def _get_cache_key(query_text: str, max_price: Any, min_sqm: Any, min_rooms: Any, top_n: Any, offset: int, only_new: bool, days: int, exclude: Any, include: Any, ex_districts: Any) -> str:
    """Generate a unique hash key for query parameters."""
    raw = f"{query_text.strip()}|{max_price}|{min_sqm}|{min_rooms}|{top_n}|{offset}|{only_new}|{days}|{exclude}|{include}|{ex_districts}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _read_cache(cache_key: str, ttl_seconds: int = 3600) -> Optional[List[Dict[str, Any]]]:
    """Retrieve cached search results if valid and not expired."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(cache_key)
        if entry and (time.time() - entry.get("timestamp", 0)) < ttl_seconds:
            return entry.get("results")
    except Exception:
        pass
    return None


def _write_cache(cache_key: str, results: List[Dict[str, Any]]) -> None:
    """Save query results to disk cache."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache: Dict[str, Any] = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        cache[cache_key] = {
            "timestamp": time.time(),
            "results": results
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def search_ideal_apartments(
    query_text: Optional[str] = None,
    max_price: Optional[float] = 750000,
    min_sqm: Optional[float] = 30,
    min_rooms: Optional[int] = 2,
    match_count: int = 500,
    match_threshold: float = 0.10,
    top_n: Optional[int] = 20,
    offset: int = 0,
    filter_agencies: bool = True,
    exclude_portals: Optional[List[str]] = None,
    include_portals: Optional[List[str]] = None,
    exclude_districts: Optional[List[str]] = None,
    only_new: bool = False,
    new_within_days: int = 3,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Query Supabase vector database with district exclusion, portal filtering, pagination, date filtering, and caching."""
    prompt = (query_text or DEFAULT_QUERY_TEXT).strip()
    active_excluded_districts = exclude_districts if exclude_districts is not None else DEFAULT_EXCLUDED_DISTRICTS

    cache_key = _get_cache_key(
        prompt, max_price, min_sqm, min_rooms, top_n, offset, only_new,
        new_within_days, exclude_portals, include_portals, active_excluded_districts
    )

    if use_cache:
        cached_results = _read_cache(cache_key)
        if cached_results is not None:
            print(f"⚡ [Cache Hit] Loaded {len(cached_results)} matching apartments from local cache (0 OpenAI/Supabase API calls).")
            return cached_results

    supabase = get_supabase_client()
    openai_client = get_openai_client()

    print("🧠 Generating 1536-dim embedding vector via OpenAI...")
    query_vector = openai_client.embeddings.create(
        input=prompt,
        model="text-embedding-3-small"
    ).data[0].embedding

    print(f"📡 Querying Supabase pgvector across entire DB (Candidate pool: {match_count} records; Caps: Max {max_price} PLN, Min {min_rooms} rooms, Min {min_sqm} m²)...")

    raw_results: List[Dict[str, Any]] = []
    try:
        rpc_params = {
            "query_embedding": query_vector,
            "match_threshold": match_threshold,
            "match_count": match_count,
        }
        if max_price is not None:
            rpc_params["p_max_price"] = max_price
        if min_sqm is not None:
            rpc_params["p_min_sqm"] = min_sqm
        if min_rooms is not None:
            rpc_params["p_min_rooms"] = min_rooms

        response = supabase.rpc("buscar_apartamentos", rpc_params).execute()
        raw_results = response.data or []
    except Exception as e:
        print(f"⚠️ RPC 'buscar_apartamentos' notice: {e}. Executing full SQL table scan...")
        query = supabase.table("apartamentos_varsovia").select("*")
        if max_price is not None:
            query = query.lte("price_pln", max_price)
        if min_sqm is not None:
            query = query.gte("sqm", min_sqm)
        if min_rooms is not None:
            query = query.gte("rooms", min_rooms)
        response = query.limit(match_count).execute()
        raw_results = response.data or []

    print(f"🔍 Retrieved {len(raw_results)} candidate records from Supabase.")

    filtered_results: List[Dict[str, Any]] = []
    excluded_agencies_count = 0
    excluded_districts_count = 0

    for apt in raw_results:
        url = (apt.get("url") or "").lower()
        ext_id = (apt.get("external_id") or "").lower()
        district_raw = (apt.get("district") or "").lower()
        title_raw = (apt.get("title") or "").lower()
        combined_location_text = f"{district_raw} {title_raw} {url}"

        # Ensure price_per_sqm is calculated if missing
        price_val = apt.get("price_pln")
        sqm_val = apt.get("sqm")
        if not apt.get("price_per_sqm") and price_val and sqm_val and sqm_val > 0:
            apt["price_per_sqm"] = round(price_val / sqm_val, 2)

        # District exclusion filter (e.g. Białołęka, Rembertów)
        if active_excluded_districts:
            norm_ex_districts = [d.lower() for d in active_excluded_districts if d]
            if any(d in combined_location_text for d in norm_ex_districts):
                excluded_districts_count += 1
                continue

        # Portal key
        portal_key = "otodom"
        if "olx.pl" in url or ext_id.startswith("olx-"):
            portal_key = "olx"
        elif "adresowo.pl" in url or ext_id.startswith("adresowo-"):
            portal_key = "adresowo"
        elif "nieruchomosci-online.pl" in url or ext_id.startswith("no-"):
            portal_key = "nieruchomosci-online"
        elif "morizon.pl" in url or ext_id.startswith("morizon-"):
            portal_key = "morizon"

        # Portal exclusion filter
        if exclude_portals:
            norm_ex = [ex.lower().replace("_", "-") for ex in exclude_portals]
            if any(ex in portal_key or ex in url or (ex == "no" and portal_key == "nieruchomosci-online") for ex in norm_ex):
                continue

        # Portal inclusion filter
        if include_portals:
            norm_inc = [inc.lower().replace("_", "-") for inc in include_portals]
            if not any(inc in portal_key or inc in url or (inc == "no" and portal_key == "nieruchomosci-online") for inc in norm_inc):
                continue

        # Agency / Developer exclusion filter
        if filter_agencies:
            desc_lower = (apt.get("description") or "").lower()
            combined_text = f"{title_raw} {desc_lower}"

            negative_keywords = [
                "stan deweloperski", "od dewelopera", "rynek pierwotny",
                "agencja nieruchomości", "biuro nieruchomości", "pobieramy prowizję"
            ]

            if any(kw in combined_text for kw in negative_keywords):
                excluded_agencies_count += 1
                continue

        filtered_results.append(apt)

    if excluded_districts_count > 0:
        print(f"🚫 Excluded {excluded_districts_count} offers from excluded districts ({', '.join(set(active_excluded_districts))}).")

    if excluded_agencies_count > 0:
        print(f"✨ Excluded {excluded_agencies_count} agency/developer offers.")

    print(f"✅ Remaining matching records: {len(filtered_results)}.")

    # Apply 'Only New' Date Filter if requested
    if only_new:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=new_within_days)
        new_only_list: List[Dict[str, Any]] = []
        for apt in filtered_results:
            created_str = apt.get("created_at") or apt.get("updated_at")
            if created_str:
                try:
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if created_dt >= cutoff_date:
                        new_only_list.append(apt)
                except Exception:
                    new_only_list.append(apt)
            else:
                new_only_list.append(apt)
        filtered_results = new_only_list
        print(f"🆕 Filtered for NEW apartments added in the last {new_within_days} days. Total new: {len(filtered_results)}.")

    # Apply Offset and Pagination Slicing
    start_idx = offset
    end_idx = (offset + top_n) if top_n is not None else None
    final_results = filtered_results[start_idx:end_idx]

    if use_cache:
        _write_cache(cache_key, final_results)

    return final_results


def generate_markdown_table(
    apartments: List[Dict[str, Any]],
    title_label: str = "Matching Apartments",
    start_index: int = 1
) -> str:
    """Formats apartment search results into a clean Markdown table with # first, then Date Posted."""
    if not apartments:
        return f"No apartments were found for '{title_label}' strictly meeting all requirements."

    md = f"### 🏡 {title_label} ({len(apartments)} displayed, items #{start_index} to #{start_index + len(apartments) - 1})\n\n"
    md += "| # | Date Posted | Portal | District | Price (PLN) | Area (m²) | Rooms | Price / m² | Green Infrastructure / Details | Direct Link |\n"
    md += "|---|---|---|---|---|---|---|---|---|---|\n"

    for idx, apt in enumerate(apartments, start=start_index):
        url = apt.get("url", "#")
        portal = "Otodom"
        if "olx.pl" in url:
            portal = "OLX"
        elif "adresowo.pl" in url:
            portal = "Adresowo"
        elif "nieruchomosci-online.pl" in url:
            portal = "Nieruchomości-online"
        elif "morizon.pl" in url:
            portal = "Morizon"

        # Format creation date as YYYY-MM-DD
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

        # Dynamically calculate price_per_sqm if missing
        if not price_sqm_val and price_val and sqm_val and sqm_val > 0:
            price_sqm_val = round(price_val / sqm_val, 2)

        price = f"{price_val:,.0f}".replace(",", " ") if price_val else "N/A"
        area = f"{sqm_val}" if sqm_val else "N/A"
        rooms = apt.get("rooms", "N/A")
        price_sqm = f"{price_sqm_val:,.0f}".replace(",", " ") if price_sqm_val else "N/A"

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

        md += f"| {idx} | {date_str} | {portal} | {district} | {price} | {area} | {rooms} | {price_sqm} | {highlight_snippet} | [View Offer]({url}) |\n"

    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Query Agent for Warsaw Apartments RAG Pipeline")
    parser.add_argument(
        "--exclude-districts",
        "--exclude-district",
        type=str,
        nargs="+",
        default=DEFAULT_EXCLUDED_DISTRICTS,
        help="Exclude specific district(s) (default: Białołęka, Rembertów)"
    )
    parser.add_argument(
        "--include-all-districts",
        action="store_true",
        help="Disable district filtering and include all Warsaw districts"
    )
    parser.add_argument(
        "--exclude-portal",
        "--exclude",
        type=str,
        nargs="+",
        help="Exclude specific portal(s) e.g. --exclude nieruchomosci-online morizon"
    )
    parser.add_argument(
        "--portals",
        type=str,
        nargs="+",
        help="Only search specific portal(s) e.g. --portals otodom olx adresowo morizon"
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Show ONLY new apartments added recently"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Number of days to classify an apartment as new when using --new (default: 3)"
    )
    parser.add_argument(
        "--full",
        "--all",
        action="store_true",
        help="Print the FULL list of matching apartments without limit"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of results to display per page (default: 20)"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of initial results to skip (e.g. --offset 100 --limit 100)"
    )
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Page number for pagination (e.g. --page 2 --limit 100)"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local cache and query Supabase fresh"
    )
    parser.add_argument(
        "--export-sheets",
        "--gsheet",
        action="store_true",
        help="Export/append matching apartment rows directly into a Google Sheet"
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        help="Google Sheet ID to append results to (defaults to GOOGLE_SHEET_ID from .env)"
    )

    args = parser.parse_args()

    offset = args.offset
    if args.page is not None and args.page > 1:
        offset = (args.page - 1) * args.limit

    top_limit = None if args.full else args.limit
    use_cache = not args.no_cache

    ex_districts = [] if args.include_all_districts else args.exclude_districts

    results = search_ideal_apartments(
        only_new=args.new,
        new_within_days=args.days,
        top_n=top_limit,
        offset=offset,
        exclude_portals=args.exclude_portal,
        include_portals=args.portals,
        exclude_districts=ex_districts,
        use_cache=use_cache
    )

    label = f"NEW Apartments (Last {args.days} Days)" if args.new else ("FULL List of Apartments" if args.full else "Matching Apartments")
    start_num = offset + 1
    print("\n" + generate_markdown_table(results, title_label=label, start_index=start_num))

    if args.export_sheets:
        from src.export_sheets import export_to_google_sheet
        export_to_google_sheet(results, sheet_id=args.sheet_id)


if __name__ == "__main__":
    main()
