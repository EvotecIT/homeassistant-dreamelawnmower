"""Shared wire and result contract for cloud interim-file signing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .exceptions import DeviceException, DreameLawnMowerCloudAPIError


def interim_file_params(
    strings: Sequence[str],
    did: str | None,
    model: str | None,
    country: str | None,
    object_name: str,
) -> dict[str, str | None]:
    """Preserve account-specific fields without logging private object names."""
    return {
        "did": str(did),
        strings[35]: model,
        strings[40]: object_name,
        strings[21]: country,
    }


def interim_file_result(response: Any, *, require_response: bool) -> Any:
    """Share absent-file and explicit-rejection semantics across transports."""
    if require_response:
        if not isinstance(response, Mapping):
            raise DeviceException("The interim-file signer did not return a response.")
        code = response.get("code")
        if isinstance(code, int) and not isinstance(code, bool) and code == 10007:
            return None
        if not isinstance(code, int) or isinstance(code, bool):
            raise DeviceException(
                "The interim-file signer response had an invalid code."
            )
        if code != 0:
            raise DreameLawnMowerCloudAPIError(code)
        if "data" not in response:
            raise DeviceException(
                "The interim-file signer response did not contain data."
            )
    if response is None or "data" not in response:
        return None
    return response["data"]
