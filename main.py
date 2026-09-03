"""Graph assembly and CLI entrypoint for the Assistant."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from config import load_settings
from llm import build_llm
from long_term_memory import DEFAULT_POOL_KWARGS, build_store
from skills import Skill, load_skills
from stats import LoggingCallbackHandler, summarize_call_stats, summarize_tool_stats
from tools import make_tools

# This assistant is designed for a single person; there is no multi-user concept.
USER_ID = "the-user"

# Key under additional_kwargs holding the real wall-clock time a message was produced,
# so the model can tell how long ago an older message in the Conversation was sent.
SENT_AT_KEY = "sent_at"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"

SYSTEM_PROMPT = (
    "You are a personal AI assistant. You may call the recall_memory tool if "
    "relevant saved facts would help you answer, and the save_memory tool when "
    "you judge a fact is worth remembering long-term. You may call the "
    "web_search tool when you need current or unknown information. You may call the "
    "execute_command tool to run shell commands in the execution environment when "
    "requested or instructed by a skill. Only call a tool when it genuinely helps the "
    "current turn; never take action the user didn't ask for.\n\n"
    "User messages may include a timestamp formatted as "
    '"<current_datetime>YYYY-MM-DDTHH:MM:SS.sssZ (Weekday)</current_datetime>" (ISO 8601 UTC). '
    "Treat the timestamp on the most recent user message as the current date and time, "
    'and use each user message\'s timestamp to interpret relative dates (e.g. "tomorrow", "today") '
    "mentioned in that message instead of dates found in tool results. Messages without "
    "a timestamp have no time information available.\n\n"
    "Never output timestamps, <current_datetime> tags, or time prefixes in your replies."
)

SKILLS_PROMPT_SECTION_TEMPLATE = (
    "\n\nAvailable skills (call load_skill with the skill's name to get its full "
    "instructions when one matches the current request; load_skill also lists any "
    "references/ or other files in that skill's directory, which you can then read "
    "with read_skill_resource if relevant):\n{skill_lines}"
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
    return datetime.now(UTC).strftime(TIMESTAMP_FORMAT)[:-3] + "Z"


def _format_datetime_tag(sent_at: str) -> str:
    try:
        dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        weekday = dt.strftime("%A")
        return f"{sent_at} ({weekday})"
    except ValueError:
        return sent_at


def _with_timestamp_prefix(message: BaseMessage) -> BaseMessage:
    if not isinstance(message, HumanMessage):
        return message
    sent_at = message.additional_kwargs.get(SENT_AT_KEY)
    if not sent_at or not isinstance(message.content, str):
        return message
    formatted_time = _format_datetime_tag(sent_at)
    return message.model_copy(
        update={
            "content": f"<current_datetime>{formatted_time}</current_datetime>\n{message.content}"
        }
    )


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


def main() -> None:
    settings = load_settings()
    llm = build_llm(settings)
    skills = load_skills(settings.skills_dir)
    tools = make_tools(settings, skills)
    handler = LoggingCallbackHandler()

    with cast(
        ConnectionPool[Connection[DictRow]],
        ConnectionPool(
            settings.database_url,
            kwargs=DEFAULT_POOL_KWARGS,
        ),
    ) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        store = build_store(pool, settings)
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
