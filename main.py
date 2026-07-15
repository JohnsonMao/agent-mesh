import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, MessagesState, START, END

load_dotenv()

def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key="lm-studio",
        model=os.getenv("LM_STUDIO_MODEL", "gemma-4-e4b"),
        temperature=0.7,
    )

def build_graph(llm: ChatOpenAI):
    def call_model(state: MessagesState):
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()

def main():
    llm = build_llm()
    app = build_graph(llm)

    user_input = "簡單介紹你自己"
    print(f"User: {user_input}\n")

    result = app.invoke({"messages": [HumanMessage(content=user_input)]})
    print(f"AI: {result['messages'][-1].content}")

if __name__ == "__main__":
    main()
