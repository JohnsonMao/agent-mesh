"""Unit tests for the Turn/Run-level stat summarizers."""

from stats import CallStat, ToolStat, summarize_call_stats, summarize_tool_stats


def test_summarize_call_stats_aggregates_counts_tokens_and_duration():
    stats = [
        CallStat(
            kind="chat",
            duration_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        CallStat(
            kind="memory_merge",
            duration_seconds=0.5,
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
        ),
    ]

    summary = summarize_call_stats(stats)

    assert "llm=1.50s (1 chat + 1 memory_merge calls)" in summary
    assert "prompt=13 completion=7 total=20" in summary


def test_summarize_call_stats_treats_missing_token_usage_as_zero():
    stats = [
        CallStat(
            kind="chat",
            duration_seconds=0.2,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    ]

    summary = summarize_call_stats(stats)

    assert "prompt=0 completion=0 total=0" in summary


def test_summarize_tool_stats_aggregates_duration_and_count():
    stats = [
        ToolStat(tool_name="add_numbers", duration_seconds=0.1),
        ToolStat(tool_name="recall_memory", duration_seconds=0.2),
    ]

    assert summarize_tool_stats(stats) == "tool=0.30s (2 calls)"
