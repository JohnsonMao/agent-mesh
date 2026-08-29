"""Runtime settings loaded from environment variables (see .env.example)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    model_provider: str
    lm_studio_base_url: str
    lm_studio_model: str
    lm_studio_embedding_model: str
    model_temperature: float
    model_max_tokens: int
    model_frequency_penalty: float
    model_presence_penalty: float
    memory_top_k: int
    memory_score_threshold: float
    checkpoint_db_path: str
    memory_store_path: str
    slack_bot_token: str
    slack_app_token: str
    slack_allowed_user_id: str


def load_settings() -> Settings:
    return Settings(
        model_provider=os.environ.get("MODEL_PROVIDER", "openai"),
        lm_studio_base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        lm_studio_model=os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b"),
        lm_studio_embedding_model=os.environ.get(
            "LM_STUDIO_EMBEDDING_MODEL", "text-embedding-bge-m3"
        ),
        model_temperature=float(os.environ.get("MODEL_TEMPERATURE", "0.2")),
        model_max_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "1024")),
        model_frequency_penalty=float(os.environ.get("MODEL_FREQUENCY_PENALTY", "0.3")),
        model_presence_penalty=float(os.environ.get("MODEL_PRESENCE_PENALTY", "0.3")),
        memory_top_k=int(os.environ.get("MEMORY_TOP_K", "5")),
        memory_score_threshold=float(os.environ.get("MEMORY_SCORE_THRESHOLD", "0.4")),
        checkpoint_db_path=os.environ.get("CHECKPOINT_DB_PATH", "data/checkpoints.sqlite"),
        memory_store_path=os.environ.get("MEMORY_STORE_PATH", "data/memory_store.sqlite"),
        slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
        slack_app_token=os.environ.get("SLACK_APP_TOKEN", ""),
        slack_allowed_user_id=os.environ.get("SLACK_ALLOWED_USER_ID", ""),
    )
