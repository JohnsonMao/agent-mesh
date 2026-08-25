"""Construction of the chat model backing the Assistant."""

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from config import Settings


def build_llm(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.lm_studio_model,
        base_url=settings.lm_studio_base_url,
        api_key=SecretStr("lm-studio"),  # LM Studio ignores the key but the client requires one
        temperature=settings.model_temperature,
        max_tokens=settings.model_max_tokens,
    )
