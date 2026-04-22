"""Wrapper module for the reusable debug OTA catalog helpers."""

from ._loader import load_internal_module

_internal = load_internal_module("debug_ota_catalog")

__all__ = [name for name in dir(_internal) if not name.startswith("_")]
globals().update({name: getattr(_internal, name) for name in __all__})
