"""Shared bootstrap for the lightweight unit-test environment."""

from __future__ import annotations

import sys
from types import ModuleType

try:
    import turbojpeg  # noqa: F401
except ModuleNotFoundError:
    turbojpeg_stub = ModuleType("turbojpeg")

    class _UnavailableTurboJPEG:
        def __init__(self) -> None:
            raise RuntimeError("TurboJPEG is unavailable in the lightweight test job")

    turbojpeg_stub.TurboJPEG = _UnavailableTurboJPEG
    sys.modules["turbojpeg"] = turbojpeg_stub
