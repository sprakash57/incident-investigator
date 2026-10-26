from pathlib import Path

import pytest
from pydantic_ai import models

from incident_investigator.demo.generate import generate


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The default-seed demo dataset, generated once per test session."""
    return generate(tmp_path_factory.mktemp("data") / "demo.db")


# Tests must never reach a real LLM provider (CI has no keys and must stay free).
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test in an empty directory so no developer `.env` (or API key) is picked up."""
    monkeypatch.chdir(tmp_path)
    for name in ("INVESTIGATOR_MODEL", "INVESTIGATOR_SQLITE_PATH", "INVESTIGATOR_RUNS_DIR"):
        monkeypatch.delenv(name, raising=False)
