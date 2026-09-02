"""Tests for PostgreSQL storage settings, store construction, and lifecycle setup."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import build_test_settings

from config import load_settings
from long_term_memory import (
    DEFAULT_POOL_KWARGS,
    build_async_store,
    build_store,
    memory_index_config,
)


def test_settings_loads_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_url = "postgresql://myuser:mypass@db.host:5432/custom_assistant"
    monkeypatch.setenv("DATABASE_URL", custom_url)
    settings = load_settings()
    assert settings.database_url == custom_url


def test_settings_has_default_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = load_settings()
    assert settings.database_url == "postgresql://postgres:postgres@localhost:5432/assistant"


def test_memory_index_config_for_test_embedding_model() -> None:
    settings = build_test_settings(lm_studio_embedding_model="test-embedding")
    config = memory_index_config(settings)
    assert config["dims"] == 64
    assert config["fields"] == ["content"]
    assert callable(config["embed"]) or hasattr(config["embed"], "embed_documents")


def test_memory_index_config_for_production_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_test_settings(lm_studio_embedding_model="text-embedding-bge-m3")
    mock_init = MagicMock()
    monkeypatch.setattr("long_term_memory.init_embeddings", mock_init)

    config = memory_index_config(settings)
    assert config["dims"] == 1024
    assert config["fields"] == ["content"]
    mock_init.assert_called_once_with(
        model="text-embedding-bge-m3",
        provider="openai",
        base_url="http://localhost:1234/v1",
        api_key="lm-studio",
        check_embedding_ctx_length=False,
    )


def test_build_store_creates_postgres_store_and_calls_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_test_settings()
    mock_conn = MagicMock()
    mock_store_instance = MagicMock()
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr("long_term_memory.PostgresStore", mock_store_cls)

    store = build_store(mock_conn, settings)

    assert store is mock_store_instance
    mock_store_cls.assert_called_once()
    mock_store_instance.setup.assert_called_once()


async def test_build_async_store_creates_async_postgres_store_and_awaits_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_test_settings()
    mock_conn = MagicMock()
    mock_store_instance = MagicMock()
    mock_store_instance.setup = AsyncMock()
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr("long_term_memory.AsyncPostgresStore", mock_store_cls)

    store = await build_async_store(mock_conn, settings)

    assert store is mock_store_instance
    mock_store_cls.assert_called_once()
    mock_store_instance.setup.assert_awaited_once()


def test_default_pool_kwargs_configuration() -> None:
    from psycopg.rows import dict_row

    assert DEFAULT_POOL_KWARGS["autocommit"] is True
    assert DEFAULT_POOL_KWARGS["prepare_threshold"] == 0
    assert DEFAULT_POOL_KWARGS["row_factory"] == dict_row
