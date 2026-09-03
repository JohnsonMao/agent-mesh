"""Secondary seam: skills.load_skills' parsing/validation boundary (see CONTEXT.md Skill)."""

import pytest

from skills import load_skills


def _write_skill(tmp_path, dir_name: str, *, name: str, description: str, body: str) -> None:
    skill_dir = tmp_path / dir_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    )


def test_load_skills_returns_name_description_body_for_a_single_skill(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "greeting",
        name="greeting",
        description="How to greet the user warmly.",
        body="Always greet enthusiastically.",
    )

    skills = load_skills(str(tmp_path))

    assert len(skills) == 1
    assert skills[0].name == "greeting"
    assert skills[0].description == "How to greet the user warmly."
    assert skills[0].body == "Always greet enthusiastically."


def test_load_skills_raises_when_directory_name_does_not_match_frontmatter_name(tmp_path) -> None:
    _write_skill(tmp_path, "greeting", name="salutation", description="desc", body="body")

    with pytest.raises(ValueError, match="greeting"):
        load_skills(str(tmp_path))


def test_load_skills_raises_when_two_skills_declare_the_same_name(tmp_path) -> None:
    _write_skill(tmp_path, "greeting", name="greeting", description="desc one", body="body one")
    _write_skill(tmp_path, "greeting-2", name="greeting", description="desc two", body="body two")

    with pytest.raises(ValueError, match="greeting"):
        load_skills(str(tmp_path))


def test_load_skills_raises_when_a_frontmatter_field_is_missing(tmp_path) -> None:
    skill_dir = tmp_path / "greeting"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: greeting\n---\n\nbody\n")

    with pytest.raises(ValueError, match="description"):
        load_skills(str(tmp_path))


def test_load_skills_records_root_and_empty_resources_for_a_single_file_skill(tmp_path) -> None:
    _write_skill(tmp_path, "greeting", name="greeting", description="desc", body="body")

    skills = load_skills(str(tmp_path))

    assert skills[0].root == tmp_path / "greeting"
    assert skills[0].resources == ()


def test_load_skills_lists_reference_and_script_files_as_resources(tmp_path) -> None:
    _write_skill(tmp_path, "text-stats", name="text-stats", description="desc", body="body")
    skill_dir = tmp_path / "text-stats"
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "output-format.md").write_text("format")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "count_stats.py").write_text("print('hi')")

    skills = load_skills(str(tmp_path))

    assert skills[0].resources == (
        "references/output-format.md",
        "scripts/count_stats.py",
    )


def test_load_skills_loads_builtin_playwright_cli_skill() -> None:
    skills = load_skills("skills")
    skill_names = [skill.name for skill in skills]

    assert "playwright-cli" in skill_names
    playwright_skill = next(s for s in skills if s.name == "playwright-cli")
    assert "playwright-cli" in playwright_skill.description
    assert "snapshot" in playwright_skill.body
