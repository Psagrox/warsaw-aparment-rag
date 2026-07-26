import logging
from typing import Any, Dict, List, Optional
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

    def upsert_apartments(self, apartments_list: List[Dict[str, Any]]) -> Any:
        """Generate embeddings for descriptions and upsert apartment records into Supabase.

        Iterates over the list of apartment dictionaries, calls the embedding module
        to populate the 'embedding' field (1536 dimensions), and performs an upsert
        into 'apartamentos_varsovia' table resolving conflicts on 'external_id'.

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

        logger.info("Processing %d apartment records for embedding generation...", len(apartments_list))
        records_to_upsert: List[Dict[str, Any]] = []

        for index, apartment in enumerate(apartments_list):
            try:
                record = apartment.copy()
                description = record.get("description")

                # Generate 1536-dimensional vector for apartment description
                record["embedding"] = self.embedder.generate_embedding(description)
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
