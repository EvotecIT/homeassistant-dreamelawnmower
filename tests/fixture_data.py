"""Helpers for loading JSON fixtures in tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_json_fixture(name: str) -> Any:
    """Load a JSON fixture by filename."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
