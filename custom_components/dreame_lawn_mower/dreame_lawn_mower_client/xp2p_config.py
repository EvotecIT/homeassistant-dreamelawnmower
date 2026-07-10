"""Tencent XP2P device-configuration request used by the Dreame app."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from .models import DreameLawnMowerCameraStreamRuntimeInputs

TENCENT_XP2P_APP_API = "https://iot.cloud.tencent.com/api/exploreropen/appapi"
TENCENT_XP2P_CONFIG_ACTION = "AppDescribeConfigureDeviceP2P"
XP2P_PROTOCOL_AUTO = 0
XP2P_PROTOCOL_UDP = 1
XP2P_PROTOCOL_TCP = 2
DEFAULT_XP2P_STUN_PORT = 20002


class DreameLawnMowerXp2pConfigError(RuntimeError):
    """Raised when Tencent does not return usable XP2P device configuration."""


class _PostResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _PostClient(Protocol):
    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        timeout: float,
    ) -> _PostResponse: ...


@dataclass(slots=True, frozen=True)
class DreameLawnMowerXp2pDeviceConfig:
    """Native app configuration returned for one Tencent IoT device."""

    server: str = ""
    ip: str = ""
    port: int = DEFAULT_XP2P_STUN_PORT
    # Dreame's working A2 Java path explicitly starts the SDK in mode 2.
    # Tencent's ABI maps that mode to TCP.
    protocol_type: int = XP2P_PROTOCOL_TCP
    cross: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return non-secret diagnostics for the native configuration."""
        return {
            "server_present": bool(self.server),
            "ip_present": bool(self.ip),
            "port": self.port,
            "protocol_type": self.protocol_type,
            "cross": self.cross,
        }


def fetch_xp2p_device_config(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    *,
    client: _PostClient | None = None,
    timeout: float = 10.0,
    timestamp: int | None = None,
    nonce: int | None = None,
    request_id: str | None = None,
) -> DreameLawnMowerXp2pDeviceConfig:
    """Reproduce the app's signed read-only device-config request."""
    missing = [
        name
        for name in ("app_id", "app_secret", "product_id", "device_name")
        if not getattr(inputs, name)
    ]
    if missing:
        raise DreameLawnMowerXp2pConfigError(
            "Cannot fetch XP2P device configuration; missing "
            + ", ".join(missing)
            + "."
        )

    now_milliseconds = int(time.time() * 1000)
    parameters: dict[str, Any] = {
        "Action": TENCENT_XP2P_CONFIG_ACTION,
        "Timestamp": int(timestamp if timestamp is not None else time.time()),
        "Nonce": int(nonce if nonce is not None else now_milliseconds),
        "AppKey": inputs.app_id,
        "ProductId": inputs.product_id,
        "DeviceName": inputs.device_name,
        "RequestId": request_id or str(uuid.uuid4()),
    }
    parameters["Signature"] = sign_xp2p_app_request(
        parameters,
        str(inputs.app_secret),
    )
    http_client = client or requests
    try:
        response = http_client.post(
            TENCENT_XP2P_APP_API,
            json=parameters,
            timeout=timeout,
        )
    except requests.RequestException as err:
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration request failed."
        ) from err
    if response.status_code != 200:
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration request returned HTTP "
            f"{response.status_code}."
        )
    try:
        payload = response.json()
    except (TypeError, ValueError) as err:
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration response was not JSON."
        ) from err
    if not isinstance(payload, Mapping):
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration response was malformed."
        )
    code = _as_int(payload.get("code"))
    if code != 0:
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration request was rejected"
            + (f" with code {code}." if code is not None else ".")
        )
    data = payload.get("data")
    config = data.get("Config") if isinstance(data, Mapping) else None
    if not isinstance(config, Mapping):
        raise DreameLawnMowerXp2pConfigError(
            "Tencent XP2P device-configuration response omitted Config."
        )
    return normalize_xp2p_device_config(config)


def resolve_xp2p_device_config(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    *,
    client: _PostClient | None = None,
    timeout: float = 10.0,
) -> DreameLawnMowerXp2pDeviceConfig:
    """Use Tencent configuration when reachable, otherwise its SDK defaults."""
    if inputs.app_credential_state != "complete":
        return DreameLawnMowerXp2pDeviceConfig()
    try:
        return fetch_xp2p_device_config(
            inputs,
            client=client,
            timeout=timeout,
        )
    except DreameLawnMowerXp2pConfigError:
        # Tencent's Android SDK invokes the listener with the same default
        # configuration when this optional cloud request fails. The A2's known
        # working path also supplies the explicit STUN file separately.
        return DreameLawnMowerXp2pDeviceConfig()


def sign_xp2p_app_request(parameters: Mapping[str, Any], app_secret: str) -> str:
    """Return the HMAC-SHA1 signature generated by Tencent's app SDK."""
    parts = []
    for key in sorted(key for key in parameters if key != "Signature"):
        value = parameters[key]
        if value is not None and str(value):
            parts.append(f"{key.replace('_', '.')}={value}")
    content = "&".join(parts).encode()
    signature = hmac.new(app_secret.encode(), content, hashlib.sha1).digest()
    return base64.b64encode(signature).decode("ascii")


def normalize_xp2p_device_config(
    value: Mapping[str, Any],
) -> DreameLawnMowerXp2pDeviceConfig:
    """Normalize the app API's Config object for the native ABI."""
    port = _as_int(value.get("StunPort")) or DEFAULT_XP2P_STUN_PORT
    protocol = str(value.get("Protocol") or "").upper()
    protocol_type = {
        "TCP": XP2P_PROTOCOL_TCP,
        "UDP": XP2P_PROTOCOL_UDP,
    }.get(protocol, XP2P_PROTOCOL_AUTO)
    return DreameLawnMowerXp2pDeviceConfig(
        server=_as_text(value.get("StunHost")),
        ip=_as_text(value.get("StunIP")),
        port=port,
        protocol_type=protocol_type,
        cross=_as_int(value.get("EnableCrossStunTurn")) == 1,
    )


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
