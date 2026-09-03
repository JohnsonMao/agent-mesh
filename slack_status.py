"""Pure helpers: Thinking Step tool label copy and Task Card content formatting."""

import re

from slack_sdk.models.blocks.block_elements import UrlSourceElement

GENERIC_LIVE_LABEL = "思考中…"

_LIVE_ACTIONS = {
    "web_search": ("🔍", "正在搜尋網路"),
    "recall_memory": ("🧠", "正在回想相關記憶"),
    "save_memory": ("📝", "正在記下新的一件事"),
    "load_skill": ("📘", "正在載入技能"),
    "read_skill_resource": ("📖", "正在讀取技能參考資料"),
    "run_skill_script": ("⚙️", "正在執行技能腳本"),
    "analyze_images": ("🖼️", "正在讀取圖片"),
    "execute_command": ("💻", "正在執行指令"),
}

_DONE_ACTIONS = {
    "web_search": ("🔍", "搜尋網路"),
    "recall_memory": ("🧠", "查詢記憶"),
    "save_memory": ("📝", "記住新事項"),
    "load_skill": ("📘", "載入技能"),
    "read_skill_resource": ("📖", "讀取技能參考資料"),
    "run_skill_script": ("⚙️", "執行技能腳本"),
    "analyze_images": ("🖼️", "分析圖片"),
    "execute_command": ("💻", "執行指令"),
}

_SAVE_MEMORY_PREFIXES = ("Saved memory: ", "Updated existing memory: ")

INPUT_MAX_LENGTH = 50
OUTPUT_MAX_LENGTH = 300


def _truncate_input(text: str) -> str:
    # Collapse multiple whitespaces and newlines into a single space
    cleaned = " ".join(text.split())
    if len(cleaned) <= INPUT_MAX_LENGTH:
        return cleaned
    return cleaned[:INPUT_MAX_LENGTH] + "…"


def extract_input_summary(tool_name: str, tool_input: object) -> str | None:
    """Extract a concise summary of the tool input for Task Card titles."""
    if not isinstance(tool_input, dict):
        return None

    summary: str | None = None
    if tool_name in ("web_search", "recall_memory"):
        summary = tool_input.get("query")
    elif tool_name == "save_memory":
        summary = tool_input.get("content")
    elif tool_name == "load_skill":
        summary = tool_input.get("name")
    elif tool_name == "read_skill_resource":
        name = tool_input.get("name")
        path = tool_input.get("relative_path")
        if name and path:
            summary = f"{name}/{path}"
        else:
            summary = name or path
    elif tool_name == "run_skill_script":
        name = tool_input.get("name")
        path = tool_input.get("script_path")
        if name and path:
            summary = f"{name}/{path}"
        else:
            summary = name or path
    elif tool_name == "execute_command":
        summary = tool_input.get("command")

    if summary is None or not str(summary).strip():
        return None
    return _truncate_input(str(summary).strip())


def tool_live_label(tool_name: str, tool_input: object = None) -> str:
    action_info = _LIVE_ACTIONS.get(tool_name)
    if not action_info:
        return GENERIC_LIVE_LABEL
    icon, action = action_info
    summary = extract_input_summary(tool_name, tool_input)
    if summary:
        # Avoid duplicate ellipsis if summary already ends with one
        sep = "" if summary.endswith("…") else "…"
        return f"{icon} {action}：{summary}{sep}"
    return f"{icon} {action}…"


def tool_done_label(tool_name: str, tool_input: object = None) -> str:
    action_info = _DONE_ACTIONS.get(tool_name)
    if not action_info:
        return tool_name
    icon, action = action_info
    summary = extract_input_summary(tool_name, tool_input)
    if summary:
        return f"{icon} {action}：{summary}"
    return f"{icon} {action}"


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_MAX_LENGTH:
        return text
    return text[:OUTPUT_MAX_LENGTH] + "…"


def build_output_summary(tool_name: str, content: str) -> str:
    """Build the Task Card `output` text for finished tool calls.

    web_search builds its output from the structured artifact instead (see
    `build_web_search_output`).
    """
    if tool_name == "save_memory":
        for prefix in _SAVE_MEMORY_PREFIXES:
            if content.startswith(prefix):
                content = content[len(prefix) :]
                break
        return _truncate(content)

    if tool_name in ("execute_command", "run_skill_script"):
        match = re.match(r"^Exit code:\s*(\d+)\n?(.*)$", content, re.DOTALL)
        if match:
            code = int(match.group(1))
            output = match.group(2).strip()
            if code == 0:
                return _truncate(output) if output else "(執行成功，無輸出)"
            return _truncate(
                f"❌ Exit code: {code}\n{output}" if output else f"❌ Exit code: {code}"
            )

    return _truncate(content)


def build_web_search_output(artifact: list[dict[str, str]]) -> str | None:
    """Build the Task Card `output` text for web_search: None if sources exist, or friendly message if empty."""
    if not artifact:
        return "找不到相關搜尋結果"
    return None


def build_sources(artifact: list[dict[str, str]]) -> list[UrlSourceElement] | None:
    """Map web_search's structured artifact to Task Card URL sources, if any."""
    if not artifact:
        return None
    return [UrlSourceElement(url=item["url"], text=item["title"]) for item in artifact]
