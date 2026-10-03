"""Database configuration, read from the environment and validated at start-up."""

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Reads `DATABASE_URL` from the environment or a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn

    @property
    def url(self) -> str:
        return str(self.database_url)
