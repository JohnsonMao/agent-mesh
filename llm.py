"""Construction of the chat model backing the Assistant."""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from config import Settings


def build_llm(settings: Settings) -> BaseChatModel:
    return init_chat_model(
        model=settings.lm_studio_model,
        model_provider=settings.model_provider,
        base_url=settings.lm_studio_base_url,
        api_key=SecretStr("lm-studio"),  # LM Studio ignores the key but the client requires one
        temperature=settings.model_temperature,
        max_tokens=settings.model_max_tokens,
    )
