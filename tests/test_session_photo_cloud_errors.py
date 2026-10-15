"""Vendor envelopes preserve rejection and documented absent-object semantics."""

import asyncio

import pytest
from aiohttp import ClientSession, web
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_files import (
    interim_file_result,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_history import (
    history_result,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCloudAPIError,
)
from tests.test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    cloud_strings,
    login_response,
    server,
)
from tests.test_session_photos import SUMMARY, _Cloud, _cloud, _entity


class _EnvelopeCloud(_Cloud):
    def __init__(self):
        fixture = _cloud()
        super().__init__(fixture.files, fixture.records)
        self.history_code = 0
        self.signer_replies = {}

    async def async_get_property_history(self, did, key, **kwargs):
        strings = [""] * 34
        strings[33] = "records"
        return history_result(
            {"code": self.history_code, "data": {"records": self.records}},
            strings,
            require_response=kwargs.get("require_response", False),
        )

    async def async_get_interim_file_url(self, did, model, object_name, **kwargs):
        response = self.signer_replies.get(object_name)
        if response is None:
            response = {
                "code": 0,
                "data": await super().async_get_interim_file_url(
                    did,
                    model,
                    object_name,
                    **kwargs,
                ),
            }
        return interim_file_result(
            response,
            require_response=kwargs.get("require_response", False),
        )


@pytest.mark.parametrize("boundary", ["history", "summary", "photo"])
def test_vendor_rejection_is_not_a_successful_empty_archive(tmp_path, boundary):
    cloud = _EnvelopeCloud()
    if boundary == "history":
        cloud.history_code = 50001
    else:
        object_name = (
            SUMMARY if boundary == "summary" else "IQ000001/-1234/ali_dreame/1.jpg"
        )
        cloud.signer_replies[object_name] = {"code": 50001}
    with pytest.raises(HomeAssistantError, match="Session photos unavailable"):
        asyncio.run(_entity(tmp_path, cloud).async_download_session_photos())


def test_documented_missing_signer_object_keeps_later_photos(tmp_path):
    cloud = _EnvelopeCloud()
    cloud.signer_replies["IQ000001/-1234/ali_dreame/1.jpg"] = {"code": 10007}
    response = asyncio.run(_entity(tmp_path, cloud).async_download_session_photos())
    assert len(response["photos"]) == 1
    assert response["photos"][0]["file"].endswith("2.jpg")
    assert response["limited"] is False


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("strict", [True, False])
def test_native_history_preserves_opt_in_rejection_contract(
    monkeypatch, account, strict
):
    strings = cloud_strings(account)

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            assert request.path == "/" + "/".join(strings[i] for i in (23, 25, 43))
            return web.json_response({"code": 50001})

        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **{**OPTIONS, "account_type": account})
            if strict:
                with pytest.raises(DreameLawnMowerCloudAPIError) as failure:
                    await cloud.async_get_property_history(
                        "42",
                        "4.1",
                        kind="event",
                        require_response=True,
                    )
                assert failure.value.code == 50001
            else:
                assert await cloud.async_get_property_history("42", "4.1") is None

    asyncio.run(scenario())
