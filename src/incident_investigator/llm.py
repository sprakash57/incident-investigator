"""Turns the configured model string into something PydanticAI can run.

Plain PydanticAI model strings (`openai:…`, `anthropic:…`, `bedrock:…`, …) pass straight
through. The one addition is non-OpenAI models on Amazon Bedrock Mantle (Bedrock's
OpenAI-compatible endpoint, which serves models such as Kimi, MiniMax and Qwen): PydanticAI's
`bedrock-mantle:` provider only accepts OpenAI models, so those are served through its generic
OpenAI chat-completions model pointed at the regional Mantle endpoint.
"""

import os

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

MANTLE_PREFIX = "bedrock-mantle:"


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
    return OpenAIChatModel(model_id, provider=provider)
