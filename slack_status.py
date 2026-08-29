"""Pure helpers: Thinking Step tool label copy and Task Card content formatting."""

from slack_sdk.models.blocks.block_elements import UrlSourceElement

GENERIC_LIVE_LABEL = "思考中…"

_LIVE_LABELS = {
    "web_search": "🔍 正在搜尋網路…",
    "recall_memory": "🧠 正在回想相關記憶…",
    "save_memory": "📝 正在記下新的一件事…",
}

_DONE_LABELS = {
    "web_search": "🔍 搜尋網路",
    "recall_memory": "🧠 查詢記憶",
    "save_memory": "📝 記住新事項",
}

_SAVE_MEMORY_PREFIXES = ("Saved memory: ", "Updated existing memory: ")

OUTPUT_MAX_LENGTH = 300


def tool_live_label(tool_name: str) -> str:
    return _LIVE_LABELS.get(tool_name, GENERIC_LIVE_LABEL)


def tool_done_label(tool_name: str) -> str:
    return _DONE_LABELS.get(tool_name, tool_name)


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_MAX_LENGTH:
        return text
    return text[:OUTPUT_MAX_LENGTH] + "…"


def build_output_summary(tool_name: str, content: str) -> str:
    """Build the Task Card `output` text for a finished save_memory/recall_memory call.

    web_search builds its output from the structured artifact instead (see
    `build_web_search_output`), since re-parsing its plain-text content here would be
    fragile if a result title happens to contain ": ".
    """
    if tool_name == "save_memory":
        for prefix in _SAVE_MEMORY_PREFIXES:
            if content.startswith(prefix):
                content = content[len(prefix) :]
                break
    return _truncate(content)


def build_web_search_output(artifact: list[dict[str, str]]) -> str:
    """Build the Task Card `output` text for web_search: one result title per line."""
    return _truncate("\n".join(item["title"] for item in artifact))


def build_sources(artifact: list[dict[str, str]]) -> list[UrlSourceElement] | None:
    """Map web_search's structured artifact to Task Card URL sources, if any."""
    if not artifact:
        return None
    return [UrlSourceElement(url=item["url"], text=item["title"]) for item in artifact]
