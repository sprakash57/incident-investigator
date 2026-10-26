import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.openai import OpenAIChatModel

from incident_investigator.llm import resolve_model


@pytest.fixture
def mantle_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-key")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-north-1")


@pytest.mark.parametrize(
    "name",
    ["openai:gpt-4o-mini", "bedrock:some.model", "bedrock-mantle:openai.gpt-oss-120b"],
)
def test_standard_model_strings_pass_through(name: str) -> None:
    assert resolve_model(name) == name


def test_non_openai_mantle_model_uses_chat_completions_endpoint(mantle_env: None) -> None:
    model = resolve_model("bedrock-mantle:moonshotai.kimi-k2.5")
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "moonshotai.kimi-k2.5"
    assert model.base_url.rstrip("/") == "https://bedrock-mantle.eu-north-1.api.aws/v1"


def test_mantle_model_without_credentials_explains_what_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("AWS_BEARER_TOKEN_BEDROCK", "AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(UserError, match="AWS_BEARER_TOKEN_BEDROCK"):
        resolve_model("bedrock-mantle:moonshotai.kimi-k2.5")


def test_mantle_models_get_their_family_profile(mantle_env: None) -> None:
    kimi = resolve_model("bedrock-mantle:moonshotai.kimi-k2.5")
    assert kimi.profile.get("supports_thinking")  # Kimi profile, not the OpenAI one
    minimax = resolve_model("bedrock-mantle:minimax.minimax-m2")
    assert isinstance(minimax, OpenAIChatModel)  # no family profile: OpenAI-compatible defaults
