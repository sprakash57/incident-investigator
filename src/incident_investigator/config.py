"""Runtime settings, loaded from environment variables and an optional .env file."""

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
    runs_dir: Path = Path("runs")


def get_settings() -> Settings:
    return Settings()
