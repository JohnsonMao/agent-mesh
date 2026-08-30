"""Pure-function tests: tool label copy, Task Card output summaries, sources."""

from slack_sdk.models.blocks.block_elements import UrlSourceElement

from slack_status import (
    build_output_summary,
    build_sources,
    build_web_search_output,
    tool_done_label,
    tool_live_label,
)


def test_tool_live_label_uses_the_per_tool_copy() -> None:
    assert tool_live_label("web_search") == "🔍 正在搜尋網路…"
    assert tool_live_label("recall_memory") == "🧠 正在回想相關記憶…"
    assert tool_live_label("save_memory") == "📝 正在記下新的一件事…"
    assert tool_live_label("read_skill_resource") == "📖 正在讀取技能參考資料…"
    assert tool_live_label("run_skill_script") == "⚙️ 正在執行技能腳本…"


def test_tool_live_label_falls_back_for_unknown_tools() -> None:
    assert tool_live_label("some_new_tool") == "思考中…"


def test_tool_done_label_uses_the_per_tool_copy() -> None:
    assert tool_done_label("web_search") == "🔍 搜尋網路"
    assert tool_done_label("recall_memory") == "🧠 查詢記憶"
    assert tool_done_label("save_memory") == "📝 記住新事項"
    assert tool_done_label("read_skill_resource") == "📖 讀取技能參考資料"
    assert tool_done_label("run_skill_script") == "⚙️ 執行技能腳本"


def test_build_output_summary_strips_the_save_memory_prefix() -> None:
    assert build_output_summary("save_memory", "Saved memory: 我下週要出差") == "我下週要出差"
    assert (
        build_output_summary("save_memory", "Updated existing memory: 我喜歡喝茶") == "我喜歡喝茶"
    )


def test_build_output_summary_keeps_recall_memory_content_as_is() -> None:
    assert build_output_summary("recall_memory", "User likes tea") == "User likes tea"
    assert (
        build_output_summary("recall_memory", "No relevant memories found.")
        == "No relevant memories found."
    )


def test_build_output_summary_truncates_long_content() -> None:
    long_content = "x" * 400
    summary = build_output_summary("recall_memory", long_content)
    assert summary == "x" * 300 + "…"


def test_build_web_search_output_joins_result_titles() -> None:
    artifact = [
        {"title": "Weather", "url": "http://example.com"},
        {"title": "Forecast", "url": "http://x.com"},
    ]
    assert build_web_search_output(artifact) == "Weather\nForecast"


def test_build_sources_maps_web_search_artifact_to_url_sources() -> None:
    artifact = [{"title": "Weather", "url": "http://example.com"}]
    assert build_sources(artifact) == [UrlSourceElement(url="http://example.com", text="Weather")]


def test_build_sources_returns_none_for_empty_artifact() -> None:
    assert build_sources([]) is None
