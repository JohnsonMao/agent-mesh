"""Print word/character/line counts for the given text as JSON."""

import json
import sys


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    print(
        json.dumps(
            {
                "words": len(text.split()),
                "characters": len(text),
                "lines": len(text.splitlines()) or (1 if text else 0),
            }
        )
    )


if __name__ == "__main__":
    main()
