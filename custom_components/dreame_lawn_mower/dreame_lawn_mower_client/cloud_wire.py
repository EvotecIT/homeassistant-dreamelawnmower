"""Shared Dreame/MOVA authentication and HTTP request formatting."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from collections.abc import Sequence

from .const import DREAME_STRINGS, MOVA_STRINGS
from .exceptions import DreameLawnMowerAuthError

DEVICE_INFO_PATH = "/dreame-user-iot/iotuserbind/device/info"
DEVICE_LIST_PATH = "/dreame-user-iot/iotuserbind/device/listV2"


def cloud_properties_params(did: str | None, keys: str) -> dict[str, str]:
    """Share device identity and normalized keys between both transports."""
    return {"did": str(did), "keys": keys}


def cloud_device_info_data(did: str | None, language: str | None) -> str:
    """Encode device identity and optional language for both cloud transports."""
    params = {"did": did}
    if language:
        params["lang"] = language
    return json.dumps(params, separators=(",", ":"))


def cloud_device_list_data(
    current: int,
    size: int,
    language: str | None,
    master: bool | None,
    shared_status: int | None,
) -> str:
    """Encode account-page filters for both cloud transports."""
    params: dict[str, int | str | bool] = {"current": current, "size": size}
    if language:
        params["lang"] = language
    if master is not None:
        params["master"] = master
    if shared_status is not None:
        params["sharedStatus"] = shared_status
    return json.dumps(params, separators=(",", ":"))


def cloud_strings(account_type: str) -> tuple[str, ...]:
    """Decode the existing account-specific protocol constants."""
    if account_type not in {"dreame", "mova"}:
        raise DreameLawnMowerAuthError(f"Unsupported account type: {account_type}")
    encoded = DREAME_STRINGS if account_type == "dreame" else MOVA_STRINGS
    values = json.loads(zlib.decompress(base64.b64decode(encoded), zlib.MAX_WBITS | 32))
    if not isinstance(values, list) or not all(
        isinstance(item, str) for item in values
    ):
        raise ValueError("Invalid cloud protocol constants")
    return tuple(values)


def cloud_headers(
    strings: Sequence[str],
    country: str,
    tenant: str | None,
) -> dict[str, str]:
    """Format shared headers without retaining account credentials."""
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        strings[47]: strings[3],
        strings[49]: strings[5],
        strings[50]: tenant or strings[6],
    }
    if country == "cn":
        headers[strings[48]] = strings[4]
    return headers


def cloud_login_data(
    strings: Sequence[str],
    username: str,
    password: str,
    refresh_token: str | None,
) -> str:
    """Preserve the vendor's credential and refresh-token wire encoding."""
    if refresh_token:
        return f"{strings[12]}{strings[13]}{refresh_token}"
    digest = hashlib.md5((password + strings[2]).encode("utf-8")).hexdigest()
    return f"{strings[12]}{strings[14]}{username}{strings[15]}{digest}{strings[16]}"
