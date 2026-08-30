"""Tools the Assistant may call: explicit long-term Memory, web search, and Skills."""

import subprocess
from pathlib import Path
from uuid import uuid4

from ddgs import DDGS
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_config, get_store
from pydantic import BaseModel, Field

from config import Settings
from llm import build_llm
from long_term_memory import memory_namespace
from skills import Skill

SKILL_SCRIPT_TIMEOUT_SECONDS = 30
SKILL_SCRIPT_OUTPUT_LIMIT = 4000

MEMORY_MERGE_PROMPT = (
    "You maintain a personal assistant's long-term memory. Merge the new fact "
    "into the existing memory, keeping everything true from both, into a single "
    "concise fact. Reply with only the merged fact, no preamble.\n\n"
    "Existing memory: {existing}\n"
    "New fact: {new}"
)


class SaveMemoryInput(BaseModel):
    content: str = Field(description="The fact to remember about the user.")


class RecallMemoryInput(BaseModel):
    query: str = Field(description="What to search for in long-term memory.")


class WebSearchInput(BaseModel):
    query: str = Field(description="The web search query.")


class LoadSkillInput(BaseModel):
    name: str = Field(description="The name of the skill to load.")


class ReadSkillResourceInput(BaseModel):
    name: str = Field(description="The name of the skill the resource belongs to.")
    relative_path: str = Field(
        description="Path to the resource file, relative to the skill's directory."
    )


class RunSkillScriptInput(BaseModel):
    name: str = Field(description="The name of the skill the script belongs to.")
    script_path: str = Field(
        description="Path to the script under the skill's scripts/ directory."
    )
    args: list[str] = Field(default_factory=list, description="Command-line arguments.")


def merge_memory_content(settings: Settings, existing: str, new: str) -> str:
    llm = build_llm(settings)
    response = llm.invoke(MEMORY_MERGE_PROMPT.format(existing=existing, new=new))
    return str(response.content).strip()


def _current_user_namespace() -> tuple[str, str]:
    config = get_config()
    user_id = config["configurable"]["user_id"]
    return memory_namespace(user_id)


def make_save_memory(settings: Settings) -> BaseTool:
    @tool("save_memory", args_schema=SaveMemoryInput)
    def save_memory(content: str) -> str:
        """Save a fact worth remembering long-term about the user."""
        store = get_store()
        namespace = _current_user_namespace()
        matches = store.search(namespace, query=content, limit=1)
        if matches and (matches[0].score or 0) >= settings.memory_score_threshold:
            existing_item = matches[0]
            merged = merge_memory_content(settings, existing_item.value["content"], content)
            store.put(namespace, existing_item.key, {"content": merged})
            return f"Updated existing memory: {merged}"
        store.put(namespace, str(uuid4()), {"content": content})
        return f"Saved memory: {content}"

    return save_memory


def make_recall_memory(settings: Settings) -> BaseTool:
    @tool("recall_memory", args_schema=RecallMemoryInput)
    def recall_memory(query: str) -> str:
        """Recall previously saved facts relevant to the query."""
        store = get_store()
        namespace = _current_user_namespace()
        matches = store.search(namespace, query=query, limit=settings.memory_top_k)
        relevant = [
            m.value["content"] for m in matches if (m.score or 0) >= settings.memory_score_threshold
        ]
        if not relevant:
            return "No relevant memories found."
        return "\n".join(relevant)

    return recall_memory


@tool("web_search", args_schema=WebSearchInput, response_format="content_and_artifact")
def web_search(query: str) -> tuple[str, list[dict[str, str]]]:
    """Search the web for current or unknown information."""
    results = DDGS().text(query, max_results=5)
    if not results:
        return "No results found.", []
    content = "\n\n".join(f"{r['title']}: {r['body']} ({r['href']})" for r in results)
    artifact = [{"title": r["title"], "url": r["href"]} for r in results]
    return content, artifact


def make_load_skill(skills: list[Skill]) -> BaseTool:
    skills_by_name = {skill.name: skill for skill in skills}

    @tool("load_skill", args_schema=LoadSkillInput)
    def load_skill(name: str) -> str:
        """Load the full instructions for a Skill by name."""
        skill = skills_by_name.get(name)
        if skill is None:
            return f"No skill named '{name}' found."
        if not skill.resources:
            return skill.body
        resource_list = "\n".join(f"- {resource}" for resource in skill.resources)
        return f"{skill.body}\n\nAvailable files in this skill's directory:\n{resource_list}"

    return load_skill


def _resolve_skill_path(skill: Skill, relative_path: str) -> Path:
    """Resolve a path within a skill's directory, rejecting escapes via '..' or symlinks."""
    root = skill.root.resolve()
    resolved = (skill.root / relative_path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path '{relative_path}' is outside skill '{skill.name}'")
    return resolved


def make_read_skill_resource(skills: list[Skill]) -> BaseTool:
    skills_by_name = {skill.name: skill for skill in skills}

    @tool("read_skill_resource", args_schema=ReadSkillResourceInput)
    def read_skill_resource(name: str, relative_path: str) -> str:
        """Read a file (e.g. under references/ or scripts/) from a Skill's directory."""
        skill = skills_by_name.get(name)
        if skill is None:
            return f"No skill named '{name}' found."
        try:
            path = _resolve_skill_path(skill, relative_path)
        except ValueError as error:
            return str(error)
        if not path.is_file():
            return f"No file '{relative_path}' found in skill '{name}'."
        return path.read_text()

    return read_skill_resource


def make_run_skill_script(skills: list[Skill]) -> BaseTool:
    skills_by_name = {skill.name: skill for skill in skills}

    @tool("run_skill_script", args_schema=RunSkillScriptInput)
    def run_skill_script(name: str, script_path: str, args: list[str]) -> str:
        """Run a Python script (e.g. under scripts/) from a Skill's directory."""
        skill = skills_by_name.get(name)
        if skill is None:
            return f"No skill named '{name}' found."
        try:
            path = _resolve_skill_path(skill, script_path)
        except ValueError as error:
            return str(error)
        if not path.is_file():
            return f"No script '{script_path}' found in skill '{name}'."
        try:
            result = subprocess.run(
                ["uv", "run", "python", str(path), *args],
                capture_output=True,
                text=True,
                timeout=SKILL_SCRIPT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return f"Script '{script_path}' timed out after {SKILL_SCRIPT_TIMEOUT_SECONDS}s."
        output = result.stdout + result.stderr
        if len(output) > SKILL_SCRIPT_OUTPUT_LIMIT:
            output = output[:SKILL_SCRIPT_OUTPUT_LIMIT] + "\n[output truncated]"
        return f"Exit code: {result.returncode}\n{output}"

    return run_skill_script


def make_tools(settings: Settings, skills: list[Skill]) -> list[BaseTool]:
    tools = [
        make_save_memory(settings),
        make_recall_memory(settings),
        web_search,
        make_load_skill(skills),
        make_read_skill_resource(skills),
    ]
    if not settings.disable_skill_scripts:
        tools.append(make_run_skill_script(skills))
    return tools
