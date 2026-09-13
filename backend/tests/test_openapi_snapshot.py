"""OpenAPI contract snapshot test."""

import json
from pathlib import Path

from app.main import app

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "openapi.json"


def test_openapi_matches_checked_in_snapshot() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert app.openapi() == expected
