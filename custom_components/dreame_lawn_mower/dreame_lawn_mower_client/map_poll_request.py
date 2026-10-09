"""Network operations selected by the existing map polling policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MapPollRequest:
    """Suspend polling state while a transport performs one selected operation."""

    kind: Literal[
        "list", "recovery", "current", "object", "cloud", "delay", "full", "changed"
    ]
    start_time: int | None = None
