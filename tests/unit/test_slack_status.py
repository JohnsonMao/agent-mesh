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
    assert tool_live_label("load_skill") == "📘 正在載入技能…"
    assert tool_live_label("read_skill_resource") == "📖 正在讀取技能參考資料…"
    assert tool_live_label("run_skill_script") == "⚙️ 正在執行技能腳本…"
    assert tool_live_label("analyze_images") == "🖼️ 正在讀取圖片…"
    assert tool_live_label("execute_command") == "💻 正在執行指令…"


def test_tool_live_label_falls_back_for_unknown_tools() -> None:
    assert tool_live_label("some_new_tool") == "思考中…"


def test_tool_done_label_uses_the_per_tool_copy() -> None:
    assert tool_done_label("web_search") == "🔍 搜尋網路"
    assert tool_done_label("recall_memory") == "🧠 查詢記憶"
    assert tool_done_label("save_memory") == "📝 記住新事項"
    assert tool_done_label("load_skill") == "📘 載入技能"
    assert tool_done_label("read_skill_resource") == "📖 讀取技能參考資料"
    assert tool_done_label("run_skill_script") == "⚙️ 執行技能腳本"
    assert tool_done_label("analyze_images") == "🖼️ 分析圖片"
    assert tool_done_label("execute_command") == "💻 執行指令"


def test_tool_live_label_with_input_summary() -> None:
    assert tool_live_label("web_search", {"query": "松山區天氣"}) == "🔍 正在搜尋網路：松山區天氣…"
    assert (
        tool_live_label("execute_command", {"command": "git status"})
        == "💻 正在執行指令：git status…"
    )
    assert (
        tool_live_label("recall_memory", {"query": "user tea preference"})
        == "🧠 正在回想相關記憶：user tea preference…"
    )
    assert (
        tool_live_label("save_memory", {"content": "我喜歡喝茶"})
        == "📝 正在記下新的一件事：我喜歡喝茶…"
    )
    assert (
        tool_live_label("load_skill", {"name": "playwright-cli"})
        == "📘 正在載入技能：playwright-cli…"
    )
    assert (
        tool_live_label(
            "read_skill_resource",
            {"name": "text-stats", "relative_path": "references/output-format.md"},
        )
        == "📖 正在讀取技能參考資料：text-stats/references/output-format.md…"
    )
    assert (
        tool_live_label(
            "run_skill_script",
            {"name": "text-stats", "script_path": "scripts/count_stats.py"},
        )
        == "⚙️ 正在執行技能腳本：text-stats/scripts/count_stats.py…"
    )


def test_tool_done_label_with_input_summary() -> None:
    assert tool_done_label("web_search", {"query": "松山區天氣"}) == "🔍 搜尋網路：松山區天氣"
    assert (
        tool_done_label("execute_command", {"command": "git status"}) == "💻 執行指令：git status"
    )


def test_tool_label_truncates_long_input_summary() -> None:
    long_query = "a" * 80
    assert tool_live_label("web_search", {"query": long_query}) == f"🔍 正在搜尋網路：{'a' * 50}…"
    assert tool_done_label("web_search", {"query": long_query}) == f"🔍 搜尋網路：{'a' * 50}…"


def test_build_output_summary_strips_the_save_memory_prefix() -> None:
    assert build_output_summary("save_memory", "Saved memory: 我下週要出差") == "我下週要出差"
    assert (
        build_output_summary("save_memory", "Updated existing memory: 我喜歡喝茶") == "我喜歡喝茶"
    )


def test_build_output_summary_handles_subprocess_output() -> None:
    assert build_output_summary("execute_command", "Exit code: 0\n") == "(執行成功，無輸出)"
    assert (
        build_output_summary("execute_command", "Exit code: 0\nAll tests passed")
        == "All tests passed"
    )
    assert (
        build_output_summary("execute_command", "Exit code: 1\ncommand not found")
        == "❌ Exit code: 1\ncommand not found"
    )
    assert build_output_summary("execute_command", "Exit code: 127\n") == "❌ Exit code: 127"
    assert build_output_summary("run_skill_script", "Exit code: 0\nDone") == "Done"


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


def test_build_web_search_output_returns_none_when_sources_present() -> None:
    artifact = [
        {"title": "Weather", "url": "http://example.com"},
        {"title": "Forecast", "url": "http://x.com"},
    ]
    assert build_web_search_output(artifact) is None


def test_build_web_search_output_returns_message_when_empty() -> None:
    assert build_web_search_output([]) == "找不到相關搜尋結果"


def test_build_sources_maps_web_search_artifact_to_url_sources() -> None:
    artifact = [{"title": "Weather", "url": "http://example.com"}]
    assert build_sources(artifact) == [UrlSourceElement(url="http://example.com", text="Weather")]


def test_build_sources_returns_none_for_empty_artifact() -> None:
    assert build_sources([]) is None
