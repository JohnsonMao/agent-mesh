import os
import re
from datetime import datetime

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

load_dotenv()

SYSTEM_PROMPT = "請一律使用繁體中文回答，不要夾雜其他語言。"

# Some local models occasionally leak a malformed tool-call as plain text instead of a proper tool_calls entry.
LEAKED_TOOL_CALL_PATTERN = re.compile(r"<\|?tool_call\|?>")
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


def build_graph(llm: BaseChatModel) -> CompiledStateGraph:
    tools = [get_current_time, add_numbers]
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        for _ in range(MAX_MODEL_RETRIES - 1):
            content = response.content if isinstance(response.content, str) else ""
            if response.tool_calls or not LEAKED_TOOL_CALL_PATTERN.search(content):
                break
            response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def main():
    llm = build_llm()
    app = build_graph(llm)

    test_inputs = [
        "現在幾點？順便幫我算 23 + 19",
        "簡單介紹你自己",
    ]

    for idx, user_input in enumerate(test_inputs, start=1):
        print(f"=== Case {idx} ===")
        print(f"User: {user_input}\n")

        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_input)]
        result = app.invoke({"messages": messages})

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
