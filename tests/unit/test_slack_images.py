"""Pure-function tests: extracting native Slack image uploads and packaging them."""

from slack_images import MAX_IMAGES, build_image_content, extract_image_files


def test_extract_image_files_keeps_only_image_mimetypes() -> None:
    files = [
        {"id": "F1", "mimetype": "image/png", "url_private": "https://x/1.png"},
        {"id": "F2", "mimetype": "application/pdf", "url_private": "https://x/1.pdf"},
        {"id": "F3", "mimetype": "image/jpeg", "url_private": "https://x/2.jpg"},
    ]
    result = extract_image_files(files)
    assert [f["id"] for f in result] == ["F1", "F3"]


def test_extract_image_files_caps_at_max_images() -> None:
    files = [{"id": str(i), "mimetype": "image/png"} for i in range(MAX_IMAGES + 2)]
    result = extract_image_files(files)
    assert len(result) == MAX_IMAGES
    assert [f["id"] for f in result] == [str(i) for i in range(MAX_IMAGES)]


def test_extract_image_files_returns_empty_list_for_no_files() -> None:
    assert extract_image_files([]) == []


def test_build_image_content_includes_text_block_and_one_image_url_block_per_image() -> None:
    images = [(b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg")]
    content = build_image_content("what is this?", images)
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_image_content_keeps_empty_text_block_when_no_text() -> None:
    content = build_image_content("", [(b"\x89PNG", "image/png")])
    assert content[0] == {"type": "text", "text": ""}
    assert len(content) == 2
