"""Prepared legacy requests preserve optional account-token wire behavior."""

import pytest
import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol_cloud,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    cloud_strings,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("token", [None, "synthetic-access-token"])
def test_optional_token_preserves_prepared_request(
    monkeypatch, account_type, method, token,
):
    """Requests prepares real headers; only the final network boundary is mocked."""
    prepared_requests = []

    def send(session, prepared, **kwargs):
        prepared_requests.append(prepared)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        response.url = prepared.url
        response.request = prepared
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "user@example.invalid", "synthetic-password", account_type=account_type,
    )
    cloud._key = token
    url = "https://vendor.example.invalid/read"
    try:
        result = (
            cloud.get(url, {"source": "typed"}, retry_count=0)
            if method == "GET"
            else cloud.request(url, {"source": "typed"}, retry_count=0)
        )
        assert result == {"ok": True}
        prepared, = prepared_requests
        assert prepared.method == method
        strings = cloud_strings(account_type)
        assert prepared.headers.get(strings[46]) == token
        assert prepared.headers[strings[47]] == strings[3]
    finally:
        cloud.disconnect()
