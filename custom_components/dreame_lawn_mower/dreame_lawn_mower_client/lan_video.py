"""Tencent XP2P same-LAN video discovery for Dreame mower cameras."""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

DEFAULT_LAN_DISCOVERY_PORT = 3072
DEFAULT_LAN_DISCOVERY_TIMEOUT = 5.0
DEFAULT_LAN_DISCOVERY_ATTEMPTS = 2
DEFAULT_LAN_DISCOVERY_VERSION = (2, 4)
DEFAULT_LAN_DISCOVERY_BROADCASTS = ("255.255.255.255",)
_PROBE_MESSAGE_TYPE = 1
_PROBE_RESPONSE_TYPE = 2
_HEADER_LENGTH = 4
_MAX_DATAGRAM_LENGTH = 65535
_MAX_PROBE_PAYLOAD_LENGTH = 1000


class DreameLawnMowerLanVideoDiscoveryError(RuntimeError):
    """Raised when LAN video discovery cannot be performed safely."""


@dataclass(slots=True, frozen=True)
class DreameLawnMowerLanVideoEndpoint:
    """One direct Tencent XP2P endpoint advertised by a mower camera."""

    product_id: str
    device_name: str
    address: str
    port: int
    response_version: str
    source: str = "udp_probe"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe endpoint summary."""
        return {
            "product_id": self.product_id,
            "device_name": self.device_name,
            "address": self.address,
            "port": self.port,
            "response_version": self.response_version,
            "source": self.source,
        }


def build_lan_video_probe_packet(
    product_id: str,
    *,
    device_name: str | None = None,
    client_token: str | None = None,
    timestamp: int | None = None,
    timeout_ms: int = 5000,
    version: tuple[int, int] = DEFAULT_LAN_DISCOVERY_VERSION,
) -> tuple[bytes, str]:
    """Build Tencent's UDP 3072 WLAN probe and return its correlation token."""
    product_id = product_id.strip()
    if not product_id:
        raise ValueError("product_id cannot be empty")
    major, minor = version
    if not 0 <= major <= 15 or not 0 <= minor <= 15:
        raise ValueError("LAN discovery version components must fit four bits")
    token = (client_token or f"ha-{secrets.token_hex(8)}").strip()
    if not token:
        raise ValueError("client_token cannot be empty")
    params = {"productId": product_id}
    if device_name is not None and (target_name := device_name.strip()):
        params["deviceName"] = target_name
    payload = json.dumps(
        {
            "method": "probe",
            "clientToken": token,
            "timestamp": int(time.time() if timestamp is None else timestamp),
            "timeoutMs": max(int(timeout_ms), 1),
            "params": params,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_PROBE_PAYLOAD_LENGTH:
        raise ValueError("LAN discovery payload is too large")
    header = bytes(
        (
            _PROBE_MESSAGE_TYPE,
            (major << 4) | minor,
            len(payload) & 0xFF,
            (len(payload) >> 8) & 0xFF,
        )
    )
    return header + payload, token


def parse_lan_video_probe_response(
    data: bytes,
    sender_address: str,
    *,
    expected_token: str,
    product_id: str,
    device_name: str | None = None,
) -> DreameLawnMowerLanVideoEndpoint | None:
    """Validate one Tencent WLAN response and return a matching endpoint."""
    if len(data) < _HEADER_LENGTH + 1 or data[0] != _PROBE_RESPONSE_TYPE:
        return None
    payload_length = data[2] | (data[3] << 8)
    if payload_length != len(data) - _HEADER_LENGTH:
        return None
    try:
        payload = json.loads(data[_HEADER_LENGTH:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("method") != "probeMatch"
        or payload.get("clientToken") != expected_token
    ):
        return None
    code = payload.get("code")
    if code not in (None, 0, "0"):
        return None
    status = str(payload.get("status") or "").strip().casefold()
    if status in {"error", "failed", "failure"}:
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    advertised_name = str(params.get("deviceName") or "").strip()
    if not advertised_name or (device_name and advertised_name != device_name):
        return None
    port = _valid_port(params.get("port"))
    if port is None:
        return None
    address = _valid_ipv4(params.get("address")) or _valid_ipv4(sender_address)
    if address is None:
        return None
    return DreameLawnMowerLanVideoEndpoint(
        product_id=product_id,
        device_name=advertised_name,
        address=address,
        port=port,
        response_version=f"{data[1] >> 4}.{data[1] & 0x0F}",
    )


def discover_lan_video_endpoints(
    product_id: str,
    *,
    device_name: str | None = None,
    client_token: str | None = None,
    timeout: float = DEFAULT_LAN_DISCOVERY_TIMEOUT,
    attempts: int = DEFAULT_LAN_DISCOVERY_ATTEMPTS,
    port: int = DEFAULT_LAN_DISCOVERY_PORT,
    broadcast_addresses: Iterable[str] = DEFAULT_LAN_DISCOVERY_BROADCASTS,
    preferred_address: str | None = None,
    bind_address: str = "",
    socket_factory: Callable[..., socket.socket] = socket.socket,
) -> tuple[DreameLawnMowerLanVideoEndpoint, ...]:
    """Discover direct mower video endpoints without contacting either cloud."""
    targets = _discovery_targets(broadcast_addresses, preferred_address)
    if not targets:
        raise ValueError("At least one LAN discovery target is required")
    discovery_port = _valid_port(port)
    if discovery_port is None:
        raise ValueError("LAN discovery port must be between 1 and 65535")
    packet, token = build_lan_video_probe_packet(
        product_id,
        device_name=device_name,
        client_token=client_token,
    )
    sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_address, discovery_port))
        except OSError as err:
            raise DreameLawnMowerLanVideoDiscoveryError(
                f"Could not bind UDP {discovery_port} for LAN video discovery."
            ) from err
        receive_timeout = min(max(float(timeout), 0.1), 0.5)
        sock.settimeout(receive_timeout)
        for _attempt in range(max(int(attempts), 1)):
            for target in targets:
                sock.sendto(packet, (target, discovery_port))

        deadline = time.monotonic() + max(float(timeout), 0.1)
        endpoints: dict[tuple[str, int, str], DreameLawnMowerLanVideoEndpoint] = {}
        while time.monotonic() < deadline:
            try:
                data, sender = sock.recvfrom(_MAX_DATAGRAM_LENGTH)
            except TimeoutError:
                continue
            except OSError as err:
                raise DreameLawnMowerLanVideoDiscoveryError(
                    "LAN video discovery receive failed."
                ) from err
            endpoint = parse_lan_video_probe_response(
                data,
                str(sender[0]),
                expected_token=token,
                product_id=product_id,
                device_name=device_name,
            )
            if endpoint is not None:
                endpoints[(endpoint.address, endpoint.port, endpoint.device_name)] = (
                    endpoint
                )
        return tuple(endpoints.values())
    finally:
        sock.close()


def discover_lan_video_endpoint(
    product_id: str,
    *,
    device_name: str,
    **kwargs: Any,
) -> DreameLawnMowerLanVideoEndpoint:
    """Return the requested direct endpoint or a clear no-advertisement error."""
    endpoints = discover_lan_video_endpoints(
        product_id,
        device_name=device_name,
        **kwargs,
    )
    if not endpoints:
        raise DreameLawnMowerLanVideoDiscoveryError(
            "The mower did not advertise a same-LAN video endpoint."
        )
    return endpoints[0]


def _discovery_targets(
    broadcast_addresses: Iterable[str],
    preferred_address: str | None,
) -> tuple[str, ...]:
    targets: list[str] = []
    if preferred_address and (address := _valid_ipv4(preferred_address)):
        targets.append(address)
    for value in broadcast_addresses:
        value = str(value).strip()
        if value and value not in targets:
            targets.append(value)
    return tuple(targets)


def _valid_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port <= 65535 else None


def _valid_ipv4(value: Any) -> str | None:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if (
        address.version != 4
        or address.is_unspecified
        or address.is_multicast
        or address.is_loopback
    ):
        return None
    return str(address)
