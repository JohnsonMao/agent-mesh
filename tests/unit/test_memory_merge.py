"""Unit tests for _merge_memory_content's dedup-merge LLM call."""

import tools


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeMergeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages, config=None):
        return _FakeResponse(self._content)


def test_merge_memory_content_returns_stripped_llm_output(monkeypatch):
    monkeypatch.setattr(tools, "build_llm", lambda: _FakeMergeLLM("  喜歡喝黑咖啡，不加糖  "))

    merged = tools._merge_memory_content("喜歡喝咖啡", "不加糖")

    assert merged == "喜歡喝黑咖啡，不加糖"


def test_merge_memory_content_falls_back_to_new_content_when_llm_output_is_blank(monkeypatch):
    monkeypatch.setattr(tools, "build_llm", lambda: _FakeMergeLLM("   "))

    merged = tools._merge_memory_content("舊記憶", "新記憶")

    assert merged == "新記憶"
