from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerLanVideoDiscoveryError,
    build_lan_video_probe_packet,
    discover_lan_video_endpoints,
    parse_lan_video_probe_response,
)


def _response(
    *,
    method: str = "probeMatch",
    token: str = "token-1",
    device_name: str = "mower-camera",
    address: str = "192.0.2.25",
    port: int = 9000,
) -> bytes:
    body = json.dumps(
        {
            "method": method,
            "clientToken": token,
            "code": 0,
            "status": "success",
            "params": {
                "deviceName": device_name,
                "address": address,
                "port": port,
            },
        },
        separators=(",", ":"),
    ).encode()
    return bytes((2, 0x24, len(body) & 0xFF, len(body) >> 8)) + body


def test_lan_probe_packet_matches_tencent_udp_3072_contract() -> None:
    packet, token = build_lan_video_probe_packet(
        "product-1",
        device_name="mower-camera",
        client_token="token-1",
        timestamp=12345,
    )

    assert token == "token-1"
    assert packet[:2] == bytes((1, 0x24))
    assert packet[2] | packet[3] << 8 == len(packet) - 4
    assert json.loads(packet[4:]) == {
        "method": "probe",
        "clientToken": "token-1",
        "timestamp": 12345,
        "timeoutMs": 5000,
        "params": {"productId": "product-1", "deviceName": "mower-camera"},
    }


def test_lan_probe_packet_enforces_tencent_payload_limit() -> None:
    with pytest.raises(ValueError, match="payload is too large"):
        build_lan_video_probe_packet("p" * 1000)


def test_lan_probe_response_returns_matching_direct_endpoint() -> None:
    endpoint = parse_lan_video_probe_response(
        _response(),
        "192.0.2.99",
        expected_token="token-1",
        product_id="product-1",
        device_name="mower-camera",
    )

    assert endpoint is not None
    assert endpoint.address == "192.0.2.25"
    assert endpoint.port == 9000
    assert endpoint.response_version == "2.4"
    assert endpoint.source == "udp_probe"


def test_lan_probe_response_uses_sender_for_unspecified_advertised_address() -> None:
    endpoint = parse_lan_video_probe_response(
        _response(address="0.0.0.0"),
        "192.0.2.99",
        expected_token="token-1",
        product_id="product-1",
        device_name="mower-camera",
    )

    assert endpoint is not None
    assert endpoint.address == "192.0.2.99"


@pytest.mark.parametrize(
    ("data", "token", "device_name"),
    [
        (_response(token="wrong"), "token-1", "mower-camera"),
        (_response(device_name="other"), "token-1", "mower-camera"),
        (_response(port=0), "token-1", "mower-camera"),
        (_response(method="notProbe"), "token-1", "mower-camera"),
        (bytes((2, 0x24, 99, 0)) + b"{}", "token-1", "mower-camera"),
    ],
)
def test_lan_probe_response_rejects_non_matching_or_invalid_advertisements(
    data: bytes,
    token: str,
    device_name: str,
) -> None:
    assert (
        parse_lan_video_probe_response(
            data,
            "192.0.2.99",
            expected_token=token,
            product_id="product-1",
            device_name=device_name,
        )
        is None
    )


class _FakeSocket:
    def __init__(
        self,
        responses: Iterable[tuple[bytes, tuple[str, int]]],
        *,
        bind_error: OSError | None = None,
    ) -> None:
        self.responses = iter(responses)
        self.bind_error = bind_error
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def setsockopt(self, *_args: object) -> None:
        return None

    def bind(self, _address: tuple[str, int]) -> None:
        if self.bind_error is not None:
            raise self.bind_error

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))

    def recvfrom(self, _length: int) -> tuple[bytes, tuple[str, int]]:
        try:
            return next(self.responses)
        except StopIteration as err:
            raise TimeoutError from err

    def close(self) -> None:
        self.closed = True


def test_lan_discovery_sends_broadcast_and_preferred_unicast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSocket([(_response(token="ha-generated"), ("192.0.2.25", 3072))])
    monkeypatch.setattr(
        "_dreame_lawn_mower_client_internal.lan_video.secrets.token_hex",
        lambda _length: "generated",
    )
    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(
        "_dreame_lawn_mower_client_internal.lan_video.time.monotonic",
        lambda: next(times, 1.0),
    )

    endpoints = discover_lan_video_endpoints(
        "product-1",
        device_name="mower-camera",
        timeout=0.1,
        attempts=1,
        preferred_address="192.0.2.25",
        socket_factory=lambda *_args: fake,
    )

    assert endpoints[0].address == "192.0.2.25"
    assert [target for _packet, target in fake.sent] == [
        ("192.0.2.25", 3072),
        ("255.255.255.255", 3072),
    ]
    assert fake.closed is True


def test_lan_discovery_uses_provisioning_token_without_retaining_it() -> None:
    fake = _FakeSocket([(_response(token="access-token"), ("192.0.2.25", 3072))])

    endpoints = discover_lan_video_endpoints(
        "product-1",
        device_name="mower-camera",
        client_token="access-token",
        timeout=0.1,
        attempts=1,
        socket_factory=lambda *_args: fake,
    )

    assert endpoints[0].address == "192.0.2.25"
    request = json.loads(fake.sent[0][0][4:])
    assert request["clientToken"] == "access-token"
    assert request["params"]["deviceName"] == "mower-camera"
    assert "access-token" not in repr(endpoints[0])


def test_lan_discovery_classifies_udp_bind_failure() -> None:
    fake = _FakeSocket([], bind_error=OSError("busy"))

    with pytest.raises(
        DreameLawnMowerLanVideoDiscoveryError,
        match="Could not bind UDP 3072",
    ):
        discover_lan_video_endpoints(
            "product-1",
            device_name="mower-camera",
            socket_factory=lambda *_args: fake,
        )

    assert fake.closed is True
