"""Secondary seam: the Skill-facing tools' file-access/execution boundary (see CONTEXT.md: Skill, Tool)."""

from conftest import build_test_settings

import tools
from skills import Skill


def _make_skill(root, resources=()) -> Skill:
    return Skill(
        name="text-stats",
        description="desc",
        body="Run scripts/count.py.",
        root=root,
        resources=resources,
    )


def test_load_skill_appends_resource_manifest_when_present(tmp_path) -> None:
    skill = _make_skill(tmp_path, resources=("references/output-format.md", "scripts/count.py"))
    load_skill = tools.make_load_skill([skill])

    result = load_skill.invoke({"name": "text-stats"})

    assert "Run scripts/count.py." in result
    assert "references/output-format.md" in result
    assert "scripts/count.py" in result


def test_load_skill_omits_manifest_when_no_resources(tmp_path) -> None:
    skill = _make_skill(tmp_path)
    load_skill = tools.make_load_skill([skill])

    result = load_skill.invoke({"name": "text-stats"})

    assert result == "Run scripts/count.py."


def test_read_skill_resource_returns_file_contents(tmp_path) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "output-format.md").write_text("format spec")
    skill = _make_skill(tmp_path, resources=("references/output-format.md",))
    read_skill_resource = tools.make_read_skill_resource([skill])

    result = read_skill_resource.invoke(
        {"name": "text-stats", "relative_path": "references/output-format.md"}
    )

    assert result == "format spec"


def test_read_skill_resource_rejects_path_traversal(tmp_path) -> None:
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope")
    skill = _make_skill(tmp_path / "text-stats")
    (tmp_path / "text-stats").mkdir()
    read_skill_resource = tools.make_read_skill_resource([skill])

    result = read_skill_resource.invoke({"name": "text-stats", "relative_path": "../secret.txt"})

    assert "outside skill" in result


def test_read_skill_resource_reports_missing_file(tmp_path) -> None:
    skill = _make_skill(tmp_path)
    read_skill_resource = tools.make_read_skill_resource([skill])

    result = read_skill_resource.invoke({"name": "text-stats", "relative_path": "missing.md"})

    assert "No file" in result


def test_make_tools_registers_execute_command(tmp_path) -> None:
    settings = build_test_settings()
    skill = _make_skill(tmp_path)

    registered = tools.make_tools(settings, [skill])

    assert "execute_command" in [tool.name for tool in registered]
    assert "read_skill_resource" in [tool.name for tool in registered]
    assert "load_skill" in [tool.name for tool in registered]


def test_web_search_returns_a_tool_result_when_the_search_backend_fails(monkeypatch) -> None:
    class FailingSearch:
        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            raise ImportError("cannot import name 'etree' from 'lxml'")

    monkeypatch.setattr(tools, "DDGS", FailingSearch)

    content, artifact = tools.web_search.func(query="current weather")

    assert "temporarily unavailable" in content
    assert artifact == []


def test_execute_command_returns_exit_code_and_output() -> None:
    result = tools.execute_command.invoke({"command": "echo 'hello world'"})

    assert "Exit code: 0" in result
    assert "hello world" in result


def test_execute_command_captures_nonzero_exit_code_and_stderr() -> None:
    result = tools.execute_command.invoke(
        {"command": "python -c \"import sys; sys.stderr.write('err msg'); sys.exit(2)\""}
    )

    assert "Exit code: 2" in result
    assert "err msg" in result


def test_execute_command_handles_timeout(monkeypatch) -> None:
    monkeypatch.setattr(tools, "COMMAND_TIMEOUT_SECONDS", 0.1)

    result = tools.execute_command.invoke({"command": 'python -c "import time; time.sleep(1)"'})

    assert "timed out after" in result


def test_execute_command_truncates_long_output() -> None:
    result = tools.execute_command.invoke({"command": "python -c \"print('a' * 5000)\""})

    assert "Exit code: 0" in result
    assert "[output truncated]" in result
