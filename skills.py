"""Loading and parsing developer-authored Skill files (see CONTEXT.md: Skill)."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def _parse_skill_md(text: str) -> tuple[dict[str, str], str]:
    _, frontmatter_block, body = text.split("---\n", 2)
    frontmatter = {}
    for line in frontmatter_block.strip().splitlines():
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body.strip()


def load_skills(skills_dir: str) -> list[Skill]:
    root = Path(skills_dir)
    if not root.is_dir():
        return []

    skills = []
    seen_names: set[str] = set()
    for skill_path in sorted(root.glob("*/SKILL.md")):
        dir_name = skill_path.parent.name
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
        skills.append(Skill(name=name, description=description, body=body))
    return skills
