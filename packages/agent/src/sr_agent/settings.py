"""Agent configuration, from `SRA_*` environment variables (or `.env`), validated at start-up.

Two secrets keep their usual names: `MCP_HTTP_TOKEN`, shared with the MCP server it calls,
and `ANTHROPIC_API_KEY`. Settings read `.env` without exporting it, so the key is passed to
the SDK explicitly; when it is not set, the SDK looks for credentials itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sr_agent.model import Effort


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SRA_", env_file=".env", extra="ignore")

    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    prompt: str = "v1"  # system prompt version (sr_agent.prompts.PROMPT_VERSIONS)
    # Retry a classifier refusal on another model in the same call ("off" to disable).
    fallback: Literal["default", "off"] = "default"
    # "summarized" returns readable summaries of the model's thinking, at the same cost.
    thinking_display: Literal["omitted", "summarized"] = "omitted"
    mcp_url: str = "http://127.0.0.1:8765/mcp"
    mcp_token: SecretStr | None = Field(default=None, validation_alias="MCP_HTTP_TOKEN")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    # Where each diagnosis is audited, as a member of sr_auditor (`make db-auditor`).
    audit_database_url: SecretStr | None = None
    # `sra serve`: the bearer token callers must send (unset: no token), how many diagnoses
    # may run at once, and browser origins allowed to call it directly (comma separated).
    api_token: SecretStr | None = None
    api_max_concurrent: int = Field(default=2, ge=1)
    api_cors_origins: str = ""
    max_tool_calls: int = Field(default=8, ge=1)
    time_limit_s: float = Field(default=60.0, gt=0)
    token_budget: int = Field(default=200_000, ge=1_000)

    @field_validator(
        "mcp_token", "anthropic_api_key", "audit_database_url", "api_token", mode="before"
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value
