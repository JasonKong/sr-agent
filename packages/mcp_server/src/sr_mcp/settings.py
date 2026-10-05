"""MCP server configuration, from `MCP_*` environment variables, validated at start-up."""

from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    # Log in as a member of the read-only sr_reader role (`sr-db create-reader`).
    database_url: PostgresDsn
    # Key for patient references (PT-…); keeps them stable within a deployment.
    mask_salt: SecretStr
    log_dir: Path = Path("var/logs")
    max_result_chars: int = Field(default=8000, ge=500)
    # Required for --transport http: the bearer token clients must send (ADR 0003).
    http_token: SecretStr | None = None
    # Embeds search queries; must match the embedder `sr-ingest` used (DESIGN.md 7.3).
    embedder: str = "bge-small"
    model_dir: Path = Path("var/models")
