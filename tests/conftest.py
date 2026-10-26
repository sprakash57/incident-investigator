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
