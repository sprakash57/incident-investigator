"""Runtime settings, loaded from environment variables and an optional .env file."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SearchProvider(StrEnum):
    DUCKDUCKGO = "duckduckgo"
    TAVILY = "tavily"
    NONE = "none"


class SandboxKind(StrEnum):
    SUBPROCESS = "subprocess"
    DOCKER = "docker"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INVESTIGATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    model: str | None = Field(
        default=None,
        description="PydanticAI model string, e.g. 'anthropic:claude-sonnet-5-5'.",
    )
    backend: str = Field(default="sqlite", description="Telemetry backend name.")
    sqlite_path: Path = Path("data/demo.db")
    search_provider: SearchProvider = SearchProvider.DUCKDUCKGO
    sandbox: SandboxKind = SandboxKind.SUBPROCESS
    max_tool_calls: int = Field(default=25, ge=1, le=200)
    max_output_tokens: int | None = Field(
        default=None,
        ge=256,
        description="Per-response output cap sent to the model. Thinking models spend part of it "
        "on reasoning; raise it to the model's maximum if responses get cut off.",
    )
    now: datetime | None = Field(
        default=None,
        description="Treat this time as 'now'. Defaults to the current time, capped at the "
        "end of the backend's data (so the demo dataset investigates its own 'morning').",
    )
    runs_dir: Path = Path("runs")


def get_settings() -> Settings:
    return Settings()
