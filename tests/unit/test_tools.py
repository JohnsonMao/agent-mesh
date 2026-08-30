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

    result = read_skill_resource.invoke(
        {"name": "text-stats", "relative_path": "../secret.txt"}
    )

    assert "outside skill" in result


def test_read_skill_resource_reports_missing_file(tmp_path) -> None:
    skill = _make_skill(tmp_path)
    read_skill_resource = tools.make_read_skill_resource([skill])

    result = read_skill_resource.invoke({"name": "text-stats", "relative_path": "missing.md"})

    assert "No file" in result


def test_run_skill_script_returns_exit_code_and_output(tmp_path) -> None:
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "echo_args.py"
    script.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n")
    skill = _make_skill(tmp_path, resources=("scripts/echo_args.py",))
    run_skill_script = tools.make_run_skill_script([skill])

    result = run_skill_script.invoke(
        {"name": "text-stats", "script_path": "scripts/echo_args.py", "args": ["hello"]}
    )

    assert "Exit code: 0" in result
    assert "hello" in result


def test_run_skill_script_rejects_path_traversal(tmp_path) -> None:
    skill_dir = tmp_path / "text-stats"
    skill_dir.mkdir()
    skill = _make_skill(skill_dir)
    run_skill_script = tools.make_run_skill_script([skill])

    result = run_skill_script.invoke(
        {"name": "text-stats", "script_path": "../../etc/passwd", "args": []}
    )

    assert "outside skill" in result


def test_make_tools_registers_run_skill_script_by_default(tmp_path) -> None:
    settings = build_test_settings()
    skill = _make_skill(tmp_path)

    registered = tools.make_tools(settings, [skill])

    assert "run_skill_script" in [tool.name for tool in registered]


def test_make_tools_omits_run_skill_script_when_disabled(tmp_path) -> None:
    settings = build_test_settings(disable_skill_scripts=True)
    skill = _make_skill(tmp_path)

    registered = tools.make_tools(settings, [skill])

    assert "run_skill_script" not in [tool.name for tool in registered]
    assert "read_skill_resource" in [tool.name for tool in registered]
