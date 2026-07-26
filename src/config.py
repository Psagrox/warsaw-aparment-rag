import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _get_required_env_var(var_name: str) -> str:
    """Retrieve and validate a required environment variable.

    Raises:
        ValueError: If the environment variable is missing or empty.
    """
    value = os.getenv(var_name)
    if not value or not value.strip():
        raise ValueError(
            f"Missing required environment variable: '{var_name}'. "
            f"Please ensure it is configured in your environment or .env file."
        )
    return value.strip()


# Configuration exports - will fail fast if any variable is missing
SUPABASE_URL: str = _get_required_env_var("SUPABASE_URL")
SUPABASE_KEY: str = _get_required_env_var("SUPABASE_KEY")
OPENAI_API_KEY: str = _get_required_env_var("OPENAI_API_KEY")
