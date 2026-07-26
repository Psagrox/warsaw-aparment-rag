import logging
from typing import List, Optional
from openai import OpenAI, OpenAIError

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Handles text vectorization using OpenAI's embedding model."""

    DEFAULT_MODEL: str = "text-embedding-3-small"
    VECTOR_DIMENSION: int = 1536

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        """Initialize the OpenAI client for generating embeddings.

        Args:
            api_key: Optional OpenAI API key. If omitted, uses OPENAI_API_KEY from config.
            model: Embedding model name (defaults to 'text-embedding-3-small').
        """
        self.model: str = model
        if api_key:
            self.client: OpenAI = OpenAI(api_key=api_key)
        else:
            from src.config import OPENAI_API_KEY
            self.client = OpenAI(api_key=OPENAI_API_KEY)

    def generate_embedding(self, text: Optional[str]) -> List[float]:
        """Generate a 1536-dimensional embedding vector for the provided text.

        Handles None, empty string, or whitespace-only inputs by returning a zero vector.

        Args:
            text: Input text string to embed.

        Returns:
            List[float]: A 1536-element float list representing the vector embedding.

        Raises:
            OpenAIError: If the OpenAI API call fails.
        """
        if text is None or not text.strip():
            logger.debug("Empty or None text provided for embedding. Returning zero vector.")
            return [0.0] * self.VECTOR_DIMENSION

        try:
            cleaned_text = text.strip()
            response = self.client.embeddings.create(
                input=cleaned_text,
                model=self.model,
            )
            embedding: List[float] = response.data[0].embedding
            return embedding
        except OpenAIError as e:
            logger.error("OpenAI API error generating embedding: %s", str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error generating embedding: %s", str(e))
            raise
