"""Centralized configuration loaded from environment variables / `.env`."""

import os

from dotenv import load_dotenv

load_dotenv()

LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "gemma-4-e4b")
LM_STUDIO_EMBEDDING_MODEL = os.getenv("LM_STUDIO_EMBEDDING_MODEL", "bge-m3")

MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.2"))
MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "1024"))
MODEL_REASONING_EFFORT = os.getenv("MODEL_REASONING_EFFORT", "none")

MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "5"))
# store.search's LIMIT always fills up to MEMORY_TOP_K regardless of relevance, so this
# cosine-similarity cutoff drops weak matches that would otherwise pollute the prompt.
MEMORY_SCORE_THRESHOLD = float(os.getenv("MEMORY_SCORE_THRESHOLD", "0.4"))
# Stricter than MEMORY_SCORE_THRESHOLD: only treat a new memory as a duplicate of an
# existing one (update in place) when similarity is at least this high.
MEMORY_DEDUP_THRESHOLD = float(os.getenv("MEMORY_DEDUP_THRESHOLD", "0.9"))
