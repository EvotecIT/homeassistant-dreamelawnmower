"""Regression checks for cloud key-definition fetch helpers."""

from __future__ import annotations

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeCloud:
    logged_in = True

    def __init__(self, content: bytes | None = None) -> None:
        self.content = content
        self.requested_url: str | None = None

    def get_file(self, url: str, retry_count: int = 4) -> bytes | None:
        self.requested_url = url
        assert retry_count == 1
        return self.content


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def test_cloud_key_definition_fetches_public_payload() -> None:
    client = _client()
    cloud = _FakeCloud(
        b'{"keyDefine":{"2.1":{"en":{"13":"Charging Completed"}}},"ver":10}'
    )
    client._sync_get_cloud_protocol = lambda: cloud
    client._sync_get_cloud_device_info = lambda language=None: {
        "keyDefine": {
            "url": "https://example.invalid/key.json",
            "ver": 10,
        }
    }

    result = client._sync_get_cloud_key_definition("en")

    assert result["url_present"] is True
    assert result["fetched"] is True
    assert result["ver"] == 10
    assert result["payload"]["keyDefine"]["2.1"]["en"]["13"] == (
        "Charging Completed"
    )
    assert cloud.requested_url == "https://example.invalid/key.json"


def test_cloud_key_definition_reports_missing_url() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeCloud()
    client._sync_get_cloud_device_info = lambda language=None: {"keyDefine": {}}

    result = client._sync_get_cloud_key_definition("en")

    assert result["url_present"] is False
    assert result["fetched"] is False
    assert result["error"] == "key_define_url_missing"
