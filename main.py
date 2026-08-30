"""Graph assembly and CLI entrypoint for the Assistant."""

import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
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

# Key under additional_kwargs holding the real wall-clock time a message was produced,
# so the model can tell how long ago an older message in the Conversation was sent.
SENT_AT_KEY = "sent_at"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

SYSTEM_PROMPT = (
    "You are a personal AI assistant. You may call the recall_memory tool if "
    "relevant saved facts would help you answer, and the save_memory tool when "
    "you judge a fact is worth remembering long-term. You may call the "
    "web_search tool when you need current or unknown information. Only call a "
    "tool when it genuinely helps the current turn; never take action the user "
    "didn't ask for.\n\n"
    "Some messages below are prefixed with a timestamp like "
    '"[YYYY-MM-DD HH:MM] ". Treat the timestamp on the most recent message as the '
    "current date and time, and use each message's own timestamp to interpret "
    'relative dates (e.g. "tomorrow", "today") mentioned in that message instead '
    "of dates found in tool results. Messages without a timestamp prefix have no "
    "time information available."
)

SKILLS_PROMPT_SECTION_TEMPLATE = (
    "\n\nAvailable skills (call load_skill with the skill's name to get its full "
    "instructions when one matches the current request; load_skill also lists any "
    "references/ or scripts/ files in that skill's directory, which you can then read "
    "with read_skill_resource or run with run_skill_script if relevant):\n{skill_lines}"
)

IMAGE_ANALYSIS_PROMPT = (
    "The user sent one or more images along with the text below (it may be empty). "
    "Analyze the image(s) using that text as context: read any visible text (OCR), "
    "describe relevant visual details, and directly answer the user's question if the "
    "image makes that possible. Respond with a concise analysis only, no preamble.\n\n"
    "User's text: {text}"
)

# Marks the analyzed text as coming from an image, not typed by the user directly.
IMAGE_ANALYSIS_MARKER = "[圖片內容：{analysis}]"


def current_sent_at() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _with_timestamp_prefix(message: BaseMessage) -> BaseMessage:
    sent_at = message.additional_kwargs.get(SENT_AT_KEY)
    if not sent_at or not isinstance(message.content, str):
        return message
    return message.model_copy(update={"content": f"[{sent_at}] {message.content}"})


def _system_prompt(skills: list[Skill]) -> str:
    prompt = SYSTEM_PROMPT
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
        messages = [
            SystemMessage(content=_system_prompt(skills)),
            *(_with_timestamp_prefix(message) for message in state["messages"]),
        ]
        response = llm_with_tools.invoke(messages, config)
        response.additional_kwargs[SENT_AT_KEY] = current_sent_at()
        return {"messages": [response]}

    return _call_model


def _tools_node(tools: list[BaseTool]) -> Callable[[MessagesState, RunnableConfig], MessagesState]:
    tool_node = ToolNode(tools)

    def _run_tools(state: MessagesState, config: RunnableConfig) -> MessagesState:
        result: MessagesState = tool_node.invoke(state, config)
        now = current_sent_at()
        for message in result["messages"]:
            message.additional_kwargs.setdefault(SENT_AT_KEY, now)
        return result

    return _run_tools


def _has_image_content(message: BaseMessage) -> bool:
    if not isinstance(message.content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "image_url" for block in message.content
    )


def _extract_text(content: Sequence[object]) -> str:
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _route_after_entry(state: MessagesState) -> str:
    messages = state["messages"]
    if messages and _has_image_content(messages[-1]):
        return "analyze_images"
    return "model"


def analyze_images(llm: BaseChatModel) -> Callable[[MessagesState, RunnableConfig], MessagesState]:
    """Mandatory Thinking Step (see CONTEXT.md, ADR 0006) that turns an image-bearing
    HumanMessage into a text-only one before it ever reaches the model or a checkpoint.
    """

    def _analyze_images(state: MessagesState, config: RunnableConfig) -> MessagesState:
        original = state["messages"][-1]
        content = original.content
        assert isinstance(content, list)
        text = _extract_text(content)
        image_blocks = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "image_url"
        ]
        prompt = [
            HumanMessage(
                content=[
                    {"type": "text", "text": IMAGE_ANALYSIS_PROMPT.format(text=text)},
                    *image_blocks,
                ]
            )
        ]
        analysis = llm.invoke(prompt, config)
        marker = IMAGE_ANALYSIS_MARKER.format(analysis=analysis.content)
        new_content = f"{text}\n\n{marker}" if text else marker
        replacement = HumanMessage(
            id=original.id,
            content=new_content,
            additional_kwargs=original.additional_kwargs,
        )
        return {"messages": [replacement]}

    return _analyze_images


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
    graph.add_node("tools", _tools_node(tools))  # type: ignore[call-overload]
    graph.add_node("analyze_images", analyze_images(llm))  # type: ignore[call-overload]
    graph.set_conditional_entry_point(_route_after_entry)
    graph.add_edge("analyze_images", "model")
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
            human_message = HumanMessage(
                content=user_input, additional_kwargs={SENT_AT_KEY: current_sent_at()}
            )
            result = app.invoke({"messages": [human_message]}, config)
            reply = result["messages"][-1]
            print(f"Assistant: {reply.content}")

    print("\n--- Run stats ---")
    print("LLM calls:", summarize_call_stats(handler.call_stats))
    print("Tool calls:", summarize_tool_stats(handler.tool_stats))


if __name__ == "__main__":
    main()
