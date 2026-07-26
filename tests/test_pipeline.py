import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.ai.embeddings
import src.db.supabase_client


class TestConfigModule(unittest.TestCase):
    """Test suite for src.config fail-fast environment variable validation."""

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_env_vars_raises_value_error(self):
        from src.config import _get_required_env_var
        with self.assertRaises(ValueError) as ctx:
            _get_required_env_var("NON_EXISTENT_VAR")
        self.assertIn("Missing required environment variable", str(ctx.exception))

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test_key",
        "OPENAI_API_KEY": "sk-test",
        "TEST_VAR": "  value123  "
    })
    def test_valid_env_var_returns_stripped_str(self):
        from src.config import _get_required_env_var
        val = _get_required_env_var("TEST_VAR")
        self.assertEqual(val, "value123")


class TestEmbeddingsModule(unittest.TestCase):
    """Test suite for src.ai.embeddings EmbeddingGenerator class."""

    def test_none_and_empty_text_returns_zero_vector(self):
        from src.ai.embeddings import EmbeddingGenerator

        embedder = EmbeddingGenerator(api_key="sk-dummy-key")

        vec_none = embedder.generate_embedding(None)
        self.assertEqual(len(vec_none), 1536)
        self.assertTrue(all(v == 0.0 for v in vec_none))

        vec_empty = embedder.generate_embedding("")
        self.assertEqual(len(vec_empty), 1536)
        self.assertTrue(all(v == 0.0 for v in vec_empty))

        vec_spaces = embedder.generate_embedding("   \n\t  ")
        self.assertEqual(len(vec_spaces), 1536)
        self.assertTrue(all(v == 0.0 for v in vec_spaces))

    @patch("src.ai.embeddings.OpenAI")
    def test_valid_text_calls_openai_client(self, mock_openai_cls):
        from src.ai.embeddings import EmbeddingGenerator

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_embedding_obj = MagicMock()
        mock_embedding_obj.embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_embedding_obj]
        mock_client.embeddings.create.return_value = mock_response

        embedder = EmbeddingGenerator(api_key="sk-dummy-key")
        result = embedder.generate_embedding("Piękne mieszkanie w Warszawie")

        self.assertEqual(len(result), 1536)
        self.assertEqual(result[0], 0.1)
        mock_client.embeddings.create.assert_called_once_with(
            input="Piękne mieszkanie w Warszawie",
            model="text-embedding-3-small"
        )


class TestSupabaseClientModule(unittest.TestCase):
    """Test suite for src.db.supabase_client SupabaseApartmentClient class."""

    @patch("src.db.supabase_client.create_client")
    def test_get_existing_ids(self, mock_create_client):
        from src.db.supabase_client import SupabaseApartmentClient

        mock_sb = MagicMock()
        mock_create_client.return_value = mock_sb
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()

        mock_sb.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.in_.return_value = mock_in

        mock_response = MagicMock()
        mock_response.data = [
            {"external_id": "olx-100"},
            {"external_id": "adresowo-200"}
        ]
        mock_in.execute.return_value = mock_response

        client = SupabaseApartmentClient(
            supabase_url="https://xyz.supabase.co",
            supabase_key="service_role_secret",
            embedder=MagicMock()
        )

        # Test empty input returns empty set
        self.assertEqual(client.get_existing_ids([]), set())

        # Test query input
        existing = client.get_existing_ids(["olx-100", "adresowo-200", "no-300"])
        self.assertEqual(existing, {"olx-100", "adresowo-200"})
        mock_sb.table.assert_called_with("apartamentos_varsovia")
        mock_table.select.assert_called_with("external_id")

    @patch("src.db.supabase_client.create_client")
    def test_upsert_apartments_flow(self, mock_create_client):
        from src.db.supabase_client import SupabaseApartmentClient

        mock_sb = MagicMock()
        mock_create_client.return_value = mock_sb
        mock_table = MagicMock()
        mock_upsert = MagicMock()
        mock_sb.table.return_value = mock_table
        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = {"status": "success"}

        mock_embedder = MagicMock()
        mock_embedder.generate_embedding.return_value = [0.5] * 1536

        client = SupabaseApartmentClient(
            supabase_url="https://xyz.supabase.co",
            supabase_key="service_role_secret",
            embedder=mock_embedder
        )

        test_apartments = [
            {
                "external_id": "oto_12345",
                "title": "Apartament Mokotów",
                "price_pln": 750000.0,
                "price_per_sqm": 15000.0,
                "sqm": 50.0,
                "rooms": 2,
                "district": "Mokotów",
                "url": "https://otodom.pl/12345",
                "description": "Jasne 2-pokojowe mieszkanie na Mokotowie."
            }
        ]

        res = client.upsert_apartments(test_apartments)

        mock_embedder.generate_embedding.assert_called_once_with("Jasne 2-pokojowe mieszkanie na Mokotowie.")
        mock_sb.table.assert_called_once_with("apartamentos_varsovia")

        upsert_args, upsert_kwargs = mock_table.upsert.call_args
        records = upsert_args[0]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["external_id"], "oto_12345")
        self.assertEqual(records[0]["embedding"], [0.5] * 1536)
        self.assertEqual(upsert_kwargs.get("on_conflict"), "external_id")
        self.assertEqual(res, {"status": "success"})


if __name__ == "__main__":
    unittest.main()
