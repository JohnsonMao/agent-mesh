import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_config, get_store
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore
from pydantic import BaseModel, Field

from checkpoint_history import CHECKPOINT_DB_PATH
from config import (
    LM_STUDIO_BASE_URL,
    LM_STUDIO_MODEL,
    MEMORY_DEDUP_THRESHOLD,
    MEMORY_SCORE_THRESHOLD,
    MEMORY_TOP_K,
    MODEL_MAX_TOKENS,
    MODEL_TEMPERATURE,
)
from long_term_memory import MEMORY_DB_PATH, memory_index_config, memory_namespace

SYSTEM_PROMPT = (
    "請一律使用繁體中文回答，不要夾雜其他語言。"
    "若需要知道使用者過去提過的偏好或事實，可呼叫 recall_memory 工具查詢，不要憑空假設。"
)

MEMORY_MERGE_PROMPT = (
    "你會收到使用者的一則舊記憶與一則新記憶，兩者語意相近但細節可能有新增或修改。"
    "請將兩者合併成一句簡潔、不重複、涵蓋所有細節的繁體中文敘述（若有衝突以新記憶為準），"
    "只輸出合併後的句子，不要加任何說明或標點以外的文字。"
)

# Some local models occasionally leak a malformed tool-call as plain text instead of a proper tool_calls entry.
LEAKED_TOOL_CALL_PATTERN = re.compile(r"<\|?tool_call\|?>")
# e.g. "<|tool_call>call:add_numbers{a:23,b:19}" -> name="add_numbers", args="a:23,b:19"
LEAKED_TOOL_CALL_DETAIL_PATTERN = re.compile(r"call:(?P<name>\w+)\{(?P<args>[^}]*)\}")
MAX_MODEL_RETRIES = 3


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


class AgentResponse(BaseModel):
    """Structured final answer returned to the caller."""

    answer: str = Field(
        ..., description="The final answer to the user's request, in Traditional Chinese."
    )
    used_tools: list[str] = Field(
        default_factory=list, description="Names of tools invoked while answering."
    )


class LoggingCallbackHandler(BaseCallbackHandler):
    """Hooks into the chat model / tool lifecycle to log execution events."""

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        print(f"[hook] model start: {len(messages[0])} messages")

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        print("[hook] model end")

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        name = serialized.get("name", "unknown")
        print(f"[hook] tool start: {name}({input_str})")

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        print(f"[hook] tool end: {output}")


@tool(args_schema=GetCurrentTimeInput)
def get_current_time() -> str:
    """Return current local time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool(args_schema=AddNumbersInput)
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


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
        if candidates and candidates[0].score is not None and candidates[0].score >= MEMORY_DEDUP_THRESHOLD
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


def build_llm() -> BaseChatModel:
    return init_chat_model(
        model=LM_STUDIO_MODEL,
        model_provider="openai",
        base_url=LM_STUDIO_BASE_URL,
        api_key="lm-studio",
        temperature=MODEL_TEMPERATURE,
        max_tokens=MODEL_MAX_TOKENS,
    )


def _merge_memory_content(old_content: str, new_content: str) -> str:
    """Ask the LLM to fold a new memory into an existing near-duplicate one."""
    response = build_llm().invoke(
        [
            SystemMessage(content=MEMORY_MERGE_PROMPT),
            HumanMessage(content=f"舊記憶：{old_content}\n新記憶：{new_content}"),
        ]
    )
    merged = response.content if isinstance(response.content, str) else ""
    return merged.strip() or new_content


def _parse_leaked_tool_call(content: str) -> dict[str, Any] | None:
    """Best-effort recovery of a malformed tool-call leaked as plain text content."""
    match = LEAKED_TOOL_CALL_DETAIL_PATTERN.search(content)
    if not match:
        return None

    args: dict[str, Any] = {}
    for pair in match.group("args").split(","):
        key, _, value = pair.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        try:
            args[key] = json.loads(value)
        except json.JSONDecodeError:
            args[key] = value
    return {"name": match.group("name"), "args": args}


def build_graph(
    llm: BaseChatModel, checkpointer: BaseCheckpointSaver, store: BaseStore
) -> CompiledStateGraph:
    tools = [get_current_time, add_numbers, save_memory, recall_memory]
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> dict:
        messages = state["messages"]

        response = llm_with_tools.invoke(messages)
        for _ in range(MAX_MODEL_RETRIES - 1):
            content = response.content if isinstance(response.content, str) else ""
            if response.tool_calls or not LEAKED_TOOL_CALL_PATTERN.search(content):
                break
            response = llm_with_tools.invoke(messages)

        content = response.content if isinstance(response.content, str) else ""
        if not response.tool_calls and LEAKED_TOOL_CALL_PATTERN.search(content):
            leaked = _parse_leaked_tool_call(content)
            if leaked:
                response = AIMessage(
                    content="",
                    tool_calls=[{**leaked, "id": str(uuid4())}],
                )
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    # Checkpointer keeps message history per thread_id; store keeps facts per user_id across threads.
    return graph.compile(checkpointer=checkpointer, store=store)


def main():
    llm = build_llm()

    user_id = "demo-user"
    test_cases = [
        ("demo-thread", "現在幾點？順便幫我算 23 + 19"),
        ("demo-thread", "我剛剛請你算的兩個數字加起來是多少？"),
        ("demo-thread", "我喜歡喝黑咖啡，不加糖，麻煩你記住這個偏好。"),
        ("demo-thread-2", "你知道我喜歡喝什麼咖啡嗎？"),
        ("demo-thread-3", "你知道我叫什麼名字嗎？"),
    ]

    # SqliteSaver persists per-thread history; SqliteStore persists per-user long-term memories.
    with (
        SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH) as checkpointer,
        SqliteStore.from_conn_string(MEMORY_DB_PATH, index=memory_index_config()) as store,
    ):
        app = build_graph(llm, checkpointer, store)

        for idx, (thread_id, user_input) in enumerate(test_cases, start=1):
            print(f"=== Case {idx} (thread={thread_id}) ===")
            print(f"User: {user_input}\n")

            config: RunnableConfig = {
                "configurable": {"thread_id": thread_id, "user_id": user_id},
                "callbacks": [LoggingCallbackHandler()],
            }

            # Check persisted checkpoint state instead of an in-process set, so reruns
            # against an existing checkpoint DB don't re-inject a duplicate system prompt.
            is_new_thread = not app.get_state(config).values.get("messages")

            messages: list[BaseMessage] = []
            if is_new_thread:
                messages.append(SystemMessage(content=SYSTEM_PROMPT))
            messages.append(HumanMessage(content=user_input))
            result = app.invoke({"messages": messages}, config=config)

            used_tools = [
                message.name
                for message in result["messages"]
                if isinstance(message, ToolMessage) and message.name
            ]
            last_message = result["messages"][-1]
            answer = last_message.content if isinstance(last_message, AIMessage) else ""
            structured = AgentResponse(answer=str(answer), used_tools=used_tools)

            print(f"Tools used: {structured.used_tools}")
            print(f"Answer: {structured.answer}\n")


if __name__ == "__main__":
    main()
