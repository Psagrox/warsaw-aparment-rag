import logging
import re
from typing import Any, Dict, List, Optional, Set
from supabase import Client, create_client
from src.ai.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


class SupabaseApartmentClient:
    """Client for vectorizing apartment descriptions and upserting into Supabase."""

    TABLE_NAME: str = "apartamentos_varsovia"

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        embedder: Optional[EmbeddingGenerator] = None,
    ) -> None:
        """Initialize connection to Supabase and setup embedding generator.

        Args:
            supabase_url: Optional Supabase URL. Defaults to SUPABASE_URL from config.
            supabase_key: Optional Supabase Key. Defaults to SUPABASE_KEY from config.
            embedder: Optional EmbeddingGenerator instance. Created automatically if omitted.
        """
        if not supabase_url or not supabase_key:
            from src.config import SUPABASE_KEY, SUPABASE_URL
            supabase_url = supabase_url or SUPABASE_URL
            supabase_key = supabase_key or SUPABASE_KEY

        try:
            self.client: Client = create_client(supabase_url, supabase_key)
            self.embedder: EmbeddingGenerator = embedder or EmbeddingGenerator()
            logger.info("Successfully initialized SupabaseApartmentClient.")
        except Exception as e:
            logger.error("Failed to initialize SupabaseApartmentClient: %s", str(e))
            raise

    def get_existing_ids(self, external_ids: List[str]) -> Set[str]:
        """Query Supabase table for external_ids and return a set of those that already exist.

        Args:
            external_ids: List of external_id strings to query in the database.

        Returns:
            Set[str]: Set of external_ids that exist in the 'apartamentos_varsovia' table.
        """
        if not external_ids:
            return set()

        unique_ids = list(set(external_ids))

        try:
            logger.info("Querying Supabase for existing external_ids (%d unique requested)...", len(unique_ids))
            response = (
                self.client.table(self.TABLE_NAME)
                .select("external_id")
                .in_("external_id", unique_ids)
                .execute()
            )

            data = response.data if response and hasattr(response, "data") else []
            existing_ids: Set[str] = {
                row["external_id"] for row in data if isinstance(row, dict) and "external_id" in row
            }
            logger.info("Found %d existing records in database.", len(existing_ids))
            return existing_ids
        except Exception as e:
            logger.error("Failed to query existing external_ids from Supabase: %s", str(e))
            raise

    def upsert_apartments(self, apartments_list: List[Dict[str, Any]]) -> Any:
        """Generate embeddings for descriptions and upsert apartment records into Supabase.

        Deduplicates input records by external_id, ensures non-null parameters (sqm, rooms, title),
        populates 1536-dim vector embeddings, and upserts into 'apartamentos_varsovia'.

        Args:
            apartments_list: List of dictionaries containing apartment metadata.

        Returns:
            The execution response from the Supabase client.

        Raises:
            Exception: If vector embedding generation or Supabase upsert fails.
        """
        if not apartments_list:
            logger.warning("upsert_apartments called with empty list. No records to upsert.")
            return []

        unique_apartments: List[Dict[str, Any]] = []
        seen_external_ids: Set[str] = set()

        for apt in apartments_list:
            ext_id = apt.get("external_id")
            if ext_id and ext_id not in seen_external_ids:
                seen_external_ids.add(ext_id)
                unique_apartments.append(apt)

        logger.info(
            "Processing %d unique apartment records (out of %d total) for embedding generation...",
            len(unique_apartments),
            len(apartments_list),
        )
        records_to_upsert: List[Dict[str, Any]] = []

        for index, apartment in enumerate(unique_apartments):
            try:
                record = apartment.copy()

                title = record.get("title")
                district_name = record.get("district") or "Warszawa"

                if not title or not str(title).strip():
                    record["title"] = f"Mieszkanie na sprzedaż ({district_name})"

                description = record.get("description") or ""
                combined_text = f"{record['title']} {description}".strip()

                # Extract sqm if missing
                if record.get("sqm") is None:
                    sqm_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m2|m²|metr)", combined_text, re.IGNORECASE)
                    if sqm_match:
                        try:
                            record["sqm"] = float(sqm_match.group(1).replace(",", "."))
                        except ValueError:
                            pass

                # Extract rooms if missing
                if record.get("rooms") is None:
                    if "kawalerka" in combined_text.lower():
                        record["rooms"] = 1
                    else:
                        rooms_match = re.search(r"(\d+)\s*(?:-| )*(?:pok|pokoj|pokój)", combined_text, re.IGNORECASE)
                        if rooms_match:
                            try:
                                record["rooms"] = int(rooms_match.group(1))
                            except ValueError:
                                pass

                # Extract price if missing
                if record.get("price_pln") is None:
                    price_match = re.search(r"(\d[\d\s\xa0\.]*)\s*(?:zł|PLN)", combined_text, re.IGNORECASE)
                    if price_match:
                        raw_price = price_match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
                        try:
                            record["price_pln"] = float(raw_price)
                        except ValueError:
                            pass

                # Reasonable default fallbacks so PostgreSQL hard caps don't drop rows
                if record.get("sqm") is None:
                    record["sqm"] = 42.0
                if record.get("rooms") is None:
                    record["rooms"] = 2

                if not description or not str(description).strip():
                    text_to_embed = f"{record['title']} {district_name}".strip()
                else:
                    text_to_embed = str(description).strip()

                record["embedding"] = self.embedder.generate_embedding(text_to_embed)
                records_to_upsert.append(record)
            except Exception as e:
                ext_id = apartment.get("external_id", f"index_{index}")
                logger.error("Error generating vector for apartment external_id '%s': %s", ext_id, str(e))
                raise

        try:
            logger.info("Executing upsert for %d records on table '%s'...", len(records_to_upsert), self.TABLE_NAME)
            response = (
                self.client.table(self.TABLE_NAME)
                .upsert(records_to_upsert, on_conflict="external_id")
                .execute()
            )
            logger.info("Upsert completed successfully for %d records.", len(records_to_upsert))
            return response
        except Exception as e:
            logger.error("Database upsert failed: %s", str(e))
            raise
