"""Load the bundled mower client implementation without importing Home Assistant."""

from __future__ import annotations

import importlib
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_INTERNAL_NAME = "_dreame_lawn_mower_client_internal"
_INTERNAL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "dreame_lawn_mower"
    / "dreame_client"
)


def _ensure_internal_package():
    module = sys.modules.get(_INTERNAL_NAME)
    if module is not None:
        return module

    spec = spec_from_file_location(
        _INTERNAL_NAME,
        _INTERNAL_ROOT / "__init__.py",
        submodule_search_locations=[str(_INTERNAL_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load bundled Dreame lawn mower client package")

    module = module_from_spec(spec)
    sys.modules[_INTERNAL_NAME] = module
    spec.loader.exec_module(module)
    return module


def load_internal_module(module_name: str | None = None):
    """Return the bundled implementation module."""

    package = _ensure_internal_package()
    if module_name is None:
        return package

    full_name = f"{_INTERNAL_NAME}.{module_name}"
    module = sys.modules.get(full_name)
    if module is not None:
        return module

    return importlib.import_module(full_name)

