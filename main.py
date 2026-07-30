import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field, SecretStr

load_dotenv()


class GetCurrentTimeInput(BaseModel):
    pass


class AddNumbersInput(BaseModel):
    a: int = Field(..., description="The first integer.")
    b: int = Field(..., description="The second integer.")


class AgentResponse(BaseModel):
    """Structured final answer returned to the caller."""

    answer: str = Field(..., description="The final answer to the user's request.")
    used_tools: list[str] = Field(
        default_factory=list, description="Names of tools invoked while answering."
    )


class AgentState(MessagesState):
    structured_response: AgentResponse | None


@tool(args_schema=GetCurrentTimeInput)
def get_current_time() -> str:
    """Return current local time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool(args_schema=AddNumbersInput)
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=SecretStr("lm-studio"),
        model=os.getenv("LM_STUDIO_MODEL", "gemma-4-e4b"),
        temperature=0.2,
    )


def build_graph(llm: ChatOpenAI) -> CompiledStateGraph:
    tools = [get_current_time, add_numbers]
    llm_with_tools = llm.bind_tools(tools)
    structured_llm = llm.with_structured_output(AgentResponse)

    def call_model(state: AgentState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def respond(state: AgentState) -> dict:
        used_tools = [
            message.name
            for message in state["messages"]
            if isinstance(message, ToolMessage) and message.name
        ]
        structured = structured_llm.invoke(state["messages"])
        structured.used_tools = used_tools
        return {"structured_response": structured}

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("respond", respond)

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition, {"tools": "tools", END: "respond"})
    graph.add_edge("tools", "model")
    graph.add_edge("respond", END)
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

        result = app.invoke({"messages": [HumanMessage(content=user_input)]})
        structured: AgentResponse = result["structured_response"]

        print(f"Tools used: {structured.used_tools}")
        print(f"Answer: {structured.answer}\n")


if __name__ == "__main__":
    main()
