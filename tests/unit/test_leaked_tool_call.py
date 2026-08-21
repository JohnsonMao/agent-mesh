"""Unit tests for parse_leaked_tool_call: best-effort recovery of a malformed tool-call
leaked as plain text content by some local models."""

from leaked_tool_call import parse_leaked_tool_call


def test_parse_leaked_tool_call_extracts_name_and_json_args():
    content = "<|tool_call|>call:add_numbers{a:23,b:19}"

    parsed = parse_leaked_tool_call(content)

    assert parsed == {"name": "add_numbers", "args": {"a": 23, "b": 19}}


def test_parse_leaked_tool_call_falls_back_to_raw_string_for_non_json_values():
    content = "call:save_memory{content:喜歡喝咖啡}"

    parsed = parse_leaked_tool_call(content)

    assert parsed == {"name": "save_memory", "args": {"content": "喜歡喝咖啡"}}


def test_parse_leaked_tool_call_returns_none_without_a_match():
    assert parse_leaked_tool_call("這是一句普通的回答，沒有工具呼叫。") is None


def test_parse_leaked_tool_call_ignores_empty_args():
    content = "call:get_current_time{}"

    parsed = parse_leaked_tool_call(content)

    assert parsed == {"name": "get_current_time", "args": {}}
