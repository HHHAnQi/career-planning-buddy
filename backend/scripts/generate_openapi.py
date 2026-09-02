"""Regenerate the checked-in OpenAPI contract snapshot."""

import json
from pathlib import Path

from app.main import app

SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tests" / "snapshots" / "openapi.json"


def main() -> None:
    """Write a deterministic UTF-8/LF OpenAPI snapshot."""
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True)
    SNAPSHOT_PATH.write_text(f"{serialized}\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
