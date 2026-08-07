import json
import os
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

from checkpoint_history import CHECKPOINT_DB_PATH

load_dotenv()

SYSTEM_PROMPT = "請一律使用繁體中文回答，不要夾雜其他語言。"

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


def build_llm() -> BaseChatModel:
    return init_chat_model(
        model=os.getenv("LM_STUDIO_MODEL", "gemma-4-e4b"),
        model_provider="openai",
        base_url=os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key="lm-studio",
        temperature=0.2,
        max_tokens=1024,
    )


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


def build_graph(llm: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    tools = [get_current_time, add_numbers]
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        for _ in range(MAX_MODEL_RETRIES - 1):
            content = response.content if isinstance(response.content, str) else ""
            if response.tool_calls or not LEAKED_TOOL_CALL_PATTERN.search(content):
                break
            response = llm_with_tools.invoke(state["messages"])

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
    # Checkpointer keeps message history per thread_id so callers don't need to resend it.
    return graph.compile(checkpointer=checkpointer)


def main():
    llm = build_llm()

    test_inputs = [
        "現在幾點？順便幫我算 23 + 19",
        "我剛剛請你算的兩個數字加起來是多少？",
    ]
    # Same thread_id lets the checkpointer restore prior turns automatically.
    config: RunnableConfig = {
        "configurable": {"thread_id": "demo-thread"},
        "callbacks": [LoggingCallbackHandler()],
    }

    # SqliteSaver persists checkpoints to a local file so history survives process restarts.
    with SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH) as checkpointer:
        app = build_graph(llm, checkpointer)

        for idx, user_input in enumerate(test_inputs, start=1):
            print(f"=== Case {idx} ===")
            print(f"User: {user_input}\n")

            messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] if idx == 1 else []
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
