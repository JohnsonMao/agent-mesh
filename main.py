"""Graph assembly and CLI entrypoint for the Assistant."""

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore

from config import Settings, load_settings
from llm import build_llm
from long_term_memory import build_store
from skills import Skill, load_skills
from stats import LoggingCallbackHandler, summarize_call_stats, summarize_tool_stats
from tools import make_tools

# This assistant is designed for a single person; there is no multi-user concept.
USER_ID = "the-user"

SYSTEM_PROMPT_TEMPLATE = (
    "You are a personal AI assistant. You may call the recall_memory tool if "
    "relevant saved facts would help you answer, and the save_memory tool when "
    "you judge a fact is worth remembering long-term. You may call the "
    "web_search tool when you need current or unknown information. Only call a "
    "tool when it genuinely helps the current turn; never take action the user "
    "didn't ask for.\n\n"
    "Current date and time: {now}. Use this as the basis for any relative date "
    "(e.g. \"tomorrow\", \"today\") instead of dates found in tool results."
)

SKILLS_PROMPT_SECTION_TEMPLATE = (
    "\n\nAvailable skills (call load_skill with the skill's name to get its full "
    "instructions when one matches the current request):\n{skill_lines}"
)


def _system_prompt(skills: list[Skill]) -> str:
    prompt = SYSTEM_PROMPT_TEMPLATE.format(now=datetime.now().strftime("%Y-%m-%d (%A) %H:%M"))
    if skills:
        skill_lines = "\n".join(
            f"- {skill.name}: {skill.description}" for skill in sorted(skills, key=lambda s: s.name)
        )
        prompt += SKILLS_PROMPT_SECTION_TEMPLATE.format(skill_lines=skill_lines)
    return prompt


def call_model(
    llm_with_tools: Runnable[LanguageModelInput, AIMessage],
    skills: list[Skill],
) -> Callable[[MessagesState, RunnableConfig], MessagesState]:
    def _call_model(state: MessagesState, config: RunnableConfig) -> MessagesState:
        messages = [SystemMessage(content=_system_prompt(skills)), *state["messages"]]
        response = llm_with_tools.invoke(messages, config)
        return {"messages": [response]}

    return _call_model


def build_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: list[Skill] | None = None,
) -> CompiledStateGraph:
    llm_with_tools = llm.bind_tools(tools)
    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model(llm_with_tools, skills or []))  # type: ignore[call-overload]
    graph.add_node("tools", ToolNode(tools))
    graph.set_entry_point("model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer, store=store)


@contextmanager
def open_store(settings: Settings) -> Generator[BaseStore]:
    # LangGraph's SqliteStore starts its own transaction via BEGIN/COMMIT inside
    # the store methods; the SQLite connection must therefore be in autocommit mode
    # instead of the default implicit-transaction mode.
    db_path = Path(settings.memory_store_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
    )
    try:
        yield build_store(conn, settings)
    finally:
        conn.close()


def main() -> None:
    settings = load_settings()
    checkpoint_path = Path(settings.checkpoint_db_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    llm = build_llm(settings)
    skills = load_skills(settings.skills_dir)
    tools = make_tools(settings, skills)
    handler = LoggingCallbackHandler()

    with (
        SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer,
        open_store(settings) as store,
    ):
        app = build_graph(llm, tools, checkpointer, store, skills)
        config: RunnableConfig = {
            "configurable": {"thread_id": "assistant", "user_id": USER_ID},
            "callbacks": [handler],
        }
        print("Personal AI Assistant (type 'exit' or 'quit' to stop)")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            result = app.invoke({"messages": [HumanMessage(content=user_input)]}, config)
            reply = result["messages"][-1]
            print(f"Assistant: {reply.content}")

    print("\n--- Run stats ---")
    print("LLM calls:", summarize_call_stats(handler.call_stats))
    print("Tool calls:", summarize_tool_stats(handler.tool_stats))


if __name__ == "__main__":
    main()
