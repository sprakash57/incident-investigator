"""Turns the configured model string into something PydanticAI can run.

Plain PydanticAI model strings (`openai:…`, `anthropic:…`, `bedrock:…`, …) pass straight
through. The one addition is non-OpenAI models on Amazon Bedrock Mantle (Bedrock's
OpenAI-compatible endpoint, which serves models such as Kimi, MiniMax and Qwen): PydanticAI's
`bedrock-mantle:` provider only accepts OpenAI models, so those are served through its generic
OpenAI chat-completions model pointed at the regional Mantle endpoint.
"""

import os
from collections.abc import Callable

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.deepseek import deepseek_model_profile
from pydantic_ai.profiles.moonshotai import moonshotai_model_profile
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer, OpenAIModelProfile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.profiles.zai import zai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

MANTLE_PREFIX = "bedrock-mantle:"

# Bedrock model ids are "<vendor>.<model>"; use PydanticAI's profile for the model family
# (how it handles thinking, tool schemas, ...) rather than the OpenAI profile.
_VENDOR_PROFILES: dict[str, Callable[[str], ModelProfile | None]] = {
    "moonshotai": moonshotai_model_profile,
    "qwen": qwen_model_profile,
    "deepseek": deepseek_model_profile,
    "zai": zai_model_profile,
}


def mantle_model_profile(model_id: str) -> ModelProfile:
    vendor, _, name = model_id.lower().partition(".")
    family = _VENDOR_PROFILES.get(vendor)
    return merge_profile(
        OpenAIModelProfile(json_schema_transformer=OpenAIJsonSchemaTransformer),
        family(name) if family else None,
    )


def mantle_base_url(region: str) -> str:
    return f"https://bedrock-mantle.{region}.api.aws/v1"


def resolve_model(name: str) -> Model | str:
    if not name.startswith(MANTLE_PREFIX):
        return name
    model_id = name.removeprefix(MANTLE_PREFIX)
    if model_id.startswith("openai."):
        return name  # PydanticAI serves OpenAI models on Mantle natively
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION")
    api_key = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
    if not region or not api_key:
        raise UserError(
            "Bedrock Mantle models need AWS_BEARER_TOKEN_BEDROCK (a Bedrock API key) and "
            "AWS_DEFAULT_REGION set in the environment or .env"
        )
    provider = OpenAIProvider(base_url=mantle_base_url(region), api_key=api_key)
    return OpenAIChatModel(model_id, provider=provider, profile=mantle_model_profile(model_id))
