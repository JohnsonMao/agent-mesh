"""Tool definitions available to the agent."""

from datetime import datetime
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.config import get_config, get_store
from pydantic import BaseModel, Field

from config import MEMORY_DEDUP_THRESHOLD, MEMORY_SCORE_THRESHOLD, MEMORY_TOP_K
from llm import build_llm
from long_term_memory import memory_namespace

MEMORY_MERGE_PROMPT = (
    "你會收到使用者的一則舊記憶與一則新記憶，兩者語意相近但細節可能有新增或修改。"
    "請將兩者合併成一句簡潔、不重複、涵蓋所有細節的繁體中文敘述（若有衝突以新記憶為準），"
    "只輸出合併後的句子，不要加任何說明或標點以外的文字。"
)


class GetCurrentTimeInput(BaseModel):
    pass


class AddNumbersInput(BaseModel):
    a: int = Field(..., description="The first integer.")
    b: int = Field(..., description="The second integer.")


class SaveMemoryInput(BaseModel):
    content: str = Field(
        ...,
        description="A fact or preference about the user worth recalling in future conversations.",
    )


class RecallMemoryInput(BaseModel):
    query: str = Field(..., description="What to look up in the user's saved facts/preferences.")


@tool(args_schema=GetCurrentTimeInput)
def get_current_time() -> str:
    """Return current local time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool(args_schema=AddNumbersInput)
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def _merge_memory_content(old_content: str, new_content: str) -> str:
    """Ask the LLM to fold a new memory into an existing near-duplicate one."""
    response = build_llm().invoke(
        [
            SystemMessage(content=MEMORY_MERGE_PROMPT),
            HumanMessage(content=f"舊記憶：{old_content}\n新記憶：{new_content}"),
        ],
        # Callbacks already reach this nested call via LangGraph's ambient config context;
        # metadata just tags it as memory_merge so stats can tell it apart from a chat call.
        config={"metadata": {"call_kind": "memory_merge"}},
    )
    merged = response.content if isinstance(response.content, str) else ""
    return merged.strip() or new_content


@tool(args_schema=SaveMemoryInput)
def save_memory(content: str) -> str:
    """Persist a fact or preference about the user, recalled across all future conversations."""
    store = get_store()
    user_id = get_config()["configurable"]["user_id"]
    namespace = memory_namespace(user_id)

    # Reuse the existing entry's key when a near-duplicate is already stored, instead of
    # letting semantically identical preferences pile up as separate UUID entries.
    candidates = store.search(namespace, query=content, limit=1)
    duplicate = (
        candidates[0]
        if candidates
        and candidates[0].score is not None
        and candidates[0].score >= MEMORY_DEDUP_THRESHOLD
        else None
    )
    if duplicate:
        merged_content = _merge_memory_content(duplicate.value["content"], content)
        store.put(namespace, duplicate.key, {"content": merged_content})
        return "已合併既有的相似記憶。"

    store.put(namespace, str(uuid4()), {"content": content})
    return "已記住這件事。"


@tool(args_schema=RecallMemoryInput)
def recall_memory(query: str) -> str:
    """Search the user's saved long-term facts/preferences relevant to the given query."""
    store = get_store()
    user_id = get_config()["configurable"]["user_id"]
    memories = store.search(memory_namespace(user_id), query=query, limit=MEMORY_TOP_K)
    relevant = [m for m in memories if m.score is None or m.score >= MEMORY_SCORE_THRESHOLD]
    if not relevant:
        return "沒有找到相關記憶。"
    return "\n".join(f"- {item.value['content']}" for item in relevant)
