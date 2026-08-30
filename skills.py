"""Loading and parsing developer-authored Skill files (see CONTEXT.md: Skill)."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    root: Path
    resources: tuple[str, ...]


def _parse_skill_md(text: str) -> tuple[dict[str, str], str]:
    _, frontmatter_block, body = text.split("---\n", 2)
    frontmatter = {}
    for line in frontmatter_block.strip().splitlines():
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body.strip()


def _skill_resources(skill_dir: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(path.relative_to(skill_dir))
            for path in skill_dir.rglob("*")
            if path.is_file() and path.name != "SKILL.md"
        )
    )


def load_skills(skills_dir: str) -> list[Skill]:
    root = Path(skills_dir)
    if not root.is_dir():
        return []

    skills = []
    seen_names: set[str] = set()
    for skill_path in sorted(root.glob("*/SKILL.md")):
        skill_dir = skill_path.parent
        dir_name = skill_dir.name
        frontmatter, body = _parse_skill_md(skill_path.read_text())
        for field in ("name", "description"):
            if field not in frontmatter:
                raise ValueError(f"Skill '{dir_name}/SKILL.md' is missing the '{field}' field")
        name = frontmatter["name"]
        description = frontmatter["description"]
        if name in seen_names:
            raise ValueError(f"Duplicate skill name '{name}' (in '{dir_name}/SKILL.md')")
        seen_names.add(name)
        if name != dir_name:
            raise ValueError(
                f"Skill directory '{dir_name}' declares name '{name}'; they must match"
            )
        skills.append(
            Skill(
                name=name,
                description=description,
                body=body,
                root=skill_dir,
                resources=_skill_resources(skill_dir),
            )
        )
    return skills
