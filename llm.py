"""Chat model construction for the local LM Studio server."""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from config import LM_STUDIO_BASE_URL, LM_STUDIO_MODEL, MODEL_MAX_TOKENS, MODEL_TEMPERATURE


def build_llm() -> BaseChatModel:
    return init_chat_model(
        model=LM_STUDIO_MODEL,
        model_provider="openai",
        base_url=LM_STUDIO_BASE_URL,
        api_key="lm-studio",
        temperature=MODEL_TEMPERATURE,
        max_tokens=MODEL_MAX_TOKENS,
        extra_body={"thinking": {"type": "disabled"}},
    )
