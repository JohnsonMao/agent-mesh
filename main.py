import os
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

@tool
def get_current_time() -> str:
    """Return current local time in a human-readable format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@tool
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b

def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key="lm-studio",
        model=os.getenv("LM_STUDIO_MODEL", "gemma-4-e4b"),
        temperature=0.2,
    )

def build_graph(llm: ChatOpenAI):
    tools = [get_current_time, add_numbers]
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: MessagesState):
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    graph.add_edge("model", END)
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
        used_tool = any(getattr(message, "type", "") == "tool" for message in result["messages"])

        print(f"Tool used: {used_tool}")
        print(f"AI: {result['messages'][-1].content}\n")

if __name__ == "__main__":
    main()
