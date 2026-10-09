"""Shared login validation and legacy MQTT identity contracts."""

import json
import time
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    cloud_auth,
    cloud_wire,
    protocol_cloud,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerAuthError,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_login_result_preserves_identity_and_short_lived_credentials(account_type):
    strings = cloud_wire.cloud_strings(account_type)
    result = cloud_auth.parse_cloud_authentication({
        strings[18]: "access-secret", strings[19]: "refresh-secret",
        strings[20]: 60, "uid": 123,
    }, strings, now=1000, tenant="tenant", region="eu")
    assert result.token == "access-secret"
    assert result.refresh_token == "refresh-secret"
    assert result.user_id == "123"
    assert result.tenant == "tenant"
    assert result.region == "eu"
    assert result.expires_at == 1030
    assert "access-secret" not in repr(result)
    assert "refresh-secret" not in repr(result)


@pytest.mark.parametrize("field,value", [
    ("access_token", ""), ("expires_in", True), ("expires_in", 0),
    ("expires_in", float("nan")), ("uid", []), ("region", {}),
])
def test_invalid_authentication_cannot_produce_partial_state(field, value):
    payload = {"access_token": "secret", "expires_in": 3600, field: value}
    with pytest.raises(DreameLawnMowerAuthError, match="response is invalid"):
        cloud_auth.parse_cloud_authentication(
            payload, cloud_wire.cloud_strings("dreame"), now=1000,
        )


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_legacy_login_uses_validated_identity_without_partial_replacement(
    monkeypatch, account_type,
):
    strings = cloud_wire.cloud_strings(account_type)
    payload = {
        strings[18]: "first-access", strings[19]: "first-refresh",
        strings[20]: 60, strings[21]: "eu", strings[22]: "tenant", "uid": 123,
    }
    session = Mock()
    session.post.side_effect = lambda *args, **kwargs: Mock(
        status_code=200, text=json.dumps(payload),
    )
    monkeypatch.setattr(protocol_cloud.requests, "session", lambda: session)
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "user@example.invalid", "password", country="eu", account_type=account_type,
    )
    try:
        assert cloud.login()
        assert cloud._uuid == "123"
        assert cloud._location == "eu"
        assert cloud._ti == "tenant"
        assert time.time() < cloud._key_expire <= time.time() + 30
        old_state = (cloud._key, cloud._secondary_key, cloud._key_expire, cloud._uuid)
        payload.update({strings[18]: "replacement-access", "uid": []})
        assert not cloud.login()
        assert not cloud.logged_in
        assert (
            cloud._key, cloud._secondary_key, cloud._key_expire, cloud._uuid
        ) == old_state
    finally:
        cloud.disconnect()
