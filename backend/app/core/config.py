"""Application settings loaded from environment variables and ``backend/.env``."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (this file lives at backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data" / "credsure.db"


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a ``CREDSURE_`` env var."""

    model_config = SettingsConfigDict(
        env_prefix="CREDSURE_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CredSure"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api"

    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    database_echo: bool = False

    # Comma-separated list in the env var, e.g. "http://localhost:5173,http://127.0.0.1:5173"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Ed25519 private keys (ADR-005, ADR-024): git-ignored directories, never the database.
    signing_key_dir: Path = BACKEND_DIR / "keys" / "platform"  # CREDSURE_SIGNING_KEY_DIR
    synthetic_key_dir: Path = BACKEND_DIR / "keys" / "synthetic"  # CREDSURE_SYNTHETIC_KEY_DIR

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
