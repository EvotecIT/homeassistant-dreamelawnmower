"""Cloud login failures must not reveal credentials in Home Assistant logs."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol_cloud,
)


def _cloud() -> protocol_cloud.DreameMowerDreameHomeCloudProtocol:
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "private-user@example.invalid", "private-password", country="eu"
    )
    strings = [""] * 53
    strings[0] = "example.invalid"
    strings[1] = "443"
    strings[2] = "private-salt"
    strings[3] = "private-client-token"
    strings[5] = "private-app-token"
    strings[6] = "private-device-token"
    strings[12] = "grant=password"
    strings[14] = "&username="
    strings[15] = "&password="
    strings[17] = "/login"
    strings[47] = "X-Client-Token"
    strings[49] = "X-App-Token"
    strings[50] = "X-Device-Token"
    cloud._strings = strings
    return cloud


def test_failed_login_omits_response_headers_and_form_data(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = _cloud()
    request = Mock(
        return_value=SimpleNamespace(
            status_code=401,
            text="private-user@example.invalid private-server-token",
        )
    )
    monkeypatch.setattr(protocol_cloud, "_post_cloud_response", request)

    with caplog.at_level(logging.ERROR, logger=protocol_cloud.__name__):
        assert cloud.login() is False

    submitted = request.call_args.args[2]
    assert "private-user@example.invalid" in submitted["data"]
    assert hashlib.md5(b"private-passwordprivate-salt").hexdigest() in submitted[
        "data"
    ]
    assert submitted["headers"]["X-Client-Token"] == "private-client-token"
    assert "Login failed: HTTP 401" in caplog.text
    for secret in (
        "private-user@example.invalid",
        "private-server-token",
        "private-client-token",
        "private-app-token",
        "private-device-token",
        hashlib.md5(b"private-passwordprivate-salt").hexdigest(),
    ):
        assert secret not in caplog.text


def test_login_transport_exception_logs_only_failure_type(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = _cloud()
    request = Mock(
        side_effect=requests.exceptions.RequestException(
            "private-user@example.invalid private-transport-token"
        )
    )
    monkeypatch.setattr(protocol_cloud, "_post_cloud_response", request)

    with caplog.at_level(logging.ERROR, logger=protocol_cloud.__name__):
        assert cloud.login() is False

    assert "Login failed: RequestException" in caplog.text
    assert "private-user@example.invalid" not in caplog.text
    assert "private-transport-token" not in caplog.text
