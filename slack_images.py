"""Pure helpers: extracting native Slack image uploads and packaging them for the model.

See CONTEXT.md and docs/adr/0006-mandatory-image-analysis-node-as-thinking-step.md for
why images are turned into text before they ever reach persisted Conversation state.
"""

import base64

MAX_IMAGES = 4

# The shape LangChain's HumanMessage.content accepts: plain text, or an OpenAI-compatible
# multimodal block list (text/image_url blocks).
MessageContent = str | list[str | dict]


def extract_image_files(files: list[dict]) -> list[dict]:
    """Return the native image uploads in `files`, capped at MAX_IMAGES."""
    images = [f for f in files if f.get("mimetype", "").startswith("image/")]
    return images[:MAX_IMAGES]


def build_image_content(text: str, images: list[tuple[bytes, str]]) -> MessageContent:
    """Build an OpenAI-compatible multimodal content block list for a HumanMessage."""
    content: list[str | dict] = [{"type": "text", "text": text}]
    for data, mimetype in images:
        encoded = base64.b64encode(data).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mimetype};base64,{encoded}"}}
        )
    return content
