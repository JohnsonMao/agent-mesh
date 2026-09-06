"""Seam: chat-model construction requests provider streamed usage."""

from conftest import build_test_settings

import llm


def test_build_llm_requests_stream_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_init_chat_model(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm, "init_chat_model", fake_init_chat_model)

    llm.build_llm(build_test_settings())

    assert captured["stream_usage"] is True
