import pytest

from incident_investigator.config import SandboxKind, SearchProvider, Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/")  # no .env picked up
    settings = Settings()
    assert settings.model is None
    assert settings.backend == "sqlite"
    assert settings.search_provider is SearchProvider.DUCKDUCKGO
    assert settings.sandbox is SandboxKind.SUBPROCESS


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVESTIGATOR_MODEL", "anthropic:claude-sonnet-5-5")
    monkeypatch.setenv("INVESTIGATOR_SEARCH_PROVIDER", "none")
    monkeypatch.setenv("INVESTIGATOR_MAX_TOOL_CALLS", "10")
    settings = Settings()
    assert settings.model == "anthropic:claude-sonnet-5-5"
    assert settings.search_provider is SearchProvider.NONE
    assert settings.max_tool_calls == 10
