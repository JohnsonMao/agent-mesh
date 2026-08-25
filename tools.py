"""Tools the Assistant may call: explicit long-term Memory and web search."""

from uuid import uuid4

from ddgs import DDGS
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_config, get_store
from pydantic import BaseModel, Field

from config import Settings
from llm import build_llm
from long_term_memory import memory_namespace

MEMORY_MERGE_PROMPT = (
    "You maintain a personal assistant's long-term memory. Merge the new fact "
    "into the existing memory, keeping everything true from both, into a single "
    "concise fact. Reply with only the merged fact, no preamble.\n\n"
    "Existing memory: {existing}\n"
    "New fact: {new}"
)


class SaveMemoryInput(BaseModel):
    content: str = Field(description="The fact to remember about the user.")


class RecallMemoryInput(BaseModel):
    query: str = Field(description="What to search for in long-term memory.")


class WebSearchInput(BaseModel):
    query: str = Field(description="The web search query.")


def merge_memory_content(settings: Settings, existing: str, new: str) -> str:
    llm = build_llm(settings)
    response = llm.invoke(MEMORY_MERGE_PROMPT.format(existing=existing, new=new))
    return str(response.content).strip()


def _current_user_namespace() -> tuple[str, str]:
    config = get_config()
    user_id = config["configurable"]["user_id"]
    return memory_namespace(user_id)


def make_save_memory(settings: Settings) -> BaseTool:
    @tool("save_memory", args_schema=SaveMemoryInput)
    def save_memory(content: str) -> str:
        """Save a fact worth remembering long-term about the user."""
        store = get_store()
        namespace = _current_user_namespace()
        matches = store.search(namespace, query=content, limit=1)
        if matches and (matches[0].score or 0) >= settings.memory_score_threshold:
            existing_item = matches[0]
            merged = merge_memory_content(settings, existing_item.value["content"], content)
            store.put(namespace, existing_item.key, {"content": merged})
            return f"Updated existing memory: {merged}"
        store.put(namespace, str(uuid4()), {"content": content})
        return f"Saved memory: {content}"

    return save_memory


def make_recall_memory(settings: Settings) -> BaseTool:
    @tool("recall_memory", args_schema=RecallMemoryInput)
    def recall_memory(query: str) -> str:
        """Recall previously saved facts relevant to the query."""
        store = get_store()
        namespace = _current_user_namespace()
        matches = store.search(namespace, query=query, limit=settings.memory_top_k)
        relevant = [
            m.value["content"] for m in matches if (m.score or 0) >= settings.memory_score_threshold
        ]
        if not relevant:
            return "No relevant memories found."
        return "\n".join(relevant)

    return recall_memory


@tool("web_search", args_schema=WebSearchInput)
def web_search(query: str) -> str:
    """Search the web for current or unknown information."""
    results = DDGS().text(query, max_results=5)
    if not results:
        return "No results found."
    return "\n\n".join(f"{r['title']}: {r['body']} ({r['href']})" for r in results)


def make_tools(settings: Settings) -> list[BaseTool]:
    return [make_save_memory(settings), make_recall_memory(settings), web_search]
