---
name: text-stats
description: Use when the user asks for word/character/line counts of a piece of text, e.g. "how many words is this?".
---

Don't count words or characters by hand — counting long text yourself is
unreliable. Instead use `execute_command` to run `uv run python skills/text-stats/scripts/count_stats.py "<text>"`
with the text as a single command-line argument; it prints the counts as JSON.

For how to format the counts back to the user, see `references/output-format.md`.
