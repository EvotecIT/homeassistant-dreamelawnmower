"""Shared runtime contract for mower video camera modules."""

from __future__ import annotations

import sys
from typing import Any, Protocol

from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)

_FACADE_MODULE = f"{__package__}.video_camera"


def _facade_binding(name: str, fallback: Any) -> Any:
    """Return a possibly monkeypatched binding from the historical facade."""
    facade = sys.modules.get(_FACADE_MODULE)
    return getattr(facade, name, fallback) if facade is not None else fallback


class _FacadeModuleProxy:
    """Resolve module attributes through a replaceable facade binding."""

    def __init__(self, name: str, fallback: Any) -> None:
        self._name = name
        self._fallback = fallback

    def __getattr__(self, name: str) -> Any:
        target = _facade_binding(self._name, self._fallback)
        return getattr(target, name)


class _DreameVideoRuntime(Protocol):
    """Runtime contract shared by native and external XP2P adapters."""

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start live video and return a local stream session."""

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop a previously started stream session."""
