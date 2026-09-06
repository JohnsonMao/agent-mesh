"""Seam: Browser State Reducer's public command-result boundary."""

from browser_state import reduce_browser_command


def test_valid_browser_result_replaces_prior_state_with_bounded_evidence() -> None:
    previous = {"url": "https://old.example", "evidence": "old evidence"}
    output = (
        """Page URL: https://example.com/products
Page Title: Products
Snapshot:
- heading \"Products\"
- link \"Buy now\" [ref=e12]
"""
        + "x" * 7_000
    )

    reduced = reduce_browser_command("playwright-cli snapshot", output, previous)

    assert reduced is not None
    assert reduced.state["url"] == "https://example.com/products"
    assert reduced.state["title"] == "Products"
    assert "e12" in reduced.state["evidence"]
    assert "old evidence" not in reduced.model_content
    assert output not in reduced.model_content


def test_targeted_evidence_replaces_older_evidence() -> None:
    previous = {"url": "https://example.com", "title": "Example", "evidence": "top of page"}

    reduced = reduce_browser_command(
        "playwright-cli snapshot '#pricing'", "Page URL: https://example.com\nPrice: $10", previous
    )

    assert reduced is not None
    assert "Price: $10" in reduced.state["evidence"]
    assert "top of page" not in reduced.state["evidence"]


def test_failed_browser_command_preserves_last_valid_state_and_records_error() -> None:
    previous = {"url": "https://example.com", "title": "Example", "evidence": "useful"}

    reduced = reduce_browser_command(
        "playwright-cli click e99", "Exit code: 1\nNo element found", previous
    )

    assert reduced is not None
    assert reduced.state["url"] == "https://example.com"
    assert reduced.state["evidence"] == "useful"
    assert "No element found" in reduced.state["recent_error"]


def test_timed_out_browser_command_preserves_last_valid_state() -> None:
    previous = {"url": "https://example.com", "title": "Example", "evidence": "useful"}

    reduced = reduce_browser_command(
        "playwright-cli snapshot",
        "Command 'playwright-cli snapshot' timed out after 60s.",
        previous,
    )

    assert reduced is not None
    assert reduced.state["evidence"] == "useful"
    assert "timed out" in reduced.state["recent_error"]


def test_non_playwright_command_is_not_reduced() -> None:
    assert reduce_browser_command("echo hello", "Exit code: 0\nhello", None) is None
