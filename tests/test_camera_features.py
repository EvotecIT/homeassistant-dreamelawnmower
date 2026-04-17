"""Regression checks for read-only camera/photo feature discovery."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_client.camera_probe import (
    build_camera_probe_payload,
)
from custom_components.dreame_lawn_mower.dreame_client.types import (
    DreameMowerAction,
    DreameMowerProperty,
    DreameMowerStreamStatus,
)
from dreame_lawn_mower_client import (
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
)


class _FakeCameraDevice:
    def __init__(self) -> None:
        self.property_mapping = {
            DreameMowerProperty.STREAM_STATUS: {"siid": 10001, "piid": 1},
            DreameMowerProperty.STREAM_AUDIO: {"siid": 10001, "piid": 2},
            DreameMowerProperty.STREAM_RECORD: {"siid": 10001, "piid": 4},
            DreameMowerProperty.TAKE_PHOTO: {"siid": 10001, "piid": 5},
            DreameMowerProperty.STREAM_KEEP_ALIVE: {"siid": 10001, "piid": 6},
            DreameMowerProperty.STREAM_FAULT: {"siid": 10001, "piid": 7},
            DreameMowerProperty.STREAM_PROPERTY: {"siid": 10001, "piid": 99},
            DreameMowerProperty.STREAM_TASK: {"siid": 10001, "piid": 103},
            DreameMowerProperty.STREAM_UPLOAD: {"siid": 10001, "piid": 1003},
            DreameMowerProperty.STREAM_CODE: {"siid": 10001, "piid": 1100},
        }
        self.action_mapping = {
            DreameMowerAction.GET_PHOTO_INFO: {"siid": 4, "aiid": 6},
            DreameMowerAction.STREAM_VIDEO: {"siid": 10001, "aiid": 1},
        }
        self.capability = SimpleNamespace(
            camera_streaming=False,
            fill_light=False,
            ai_detection=True,
            obstacles=False,
        )
        self.status = SimpleNamespace(
            stream_session="session-1",
            stream_status=DreameMowerStreamStatus.VIDEO,
        )
        self.info = SimpleNamespace(
            raw={
                "deviceInfo": {
                    "feature": "video_tx",
                    "permit": "pincode,video,aiobs",
                    "extendScType": ["PINCODE"],
                    "liveKeyDefine": {"monitor": "available"},
                    "videoStatus": "ready",
                    "videoDynamicVendor": False,
                }
            }
        )
        self.data = {
            DreameMowerProperty.STREAM_STATUS.value: (
                '{"result":0,"session":"session-1","operType":"start",'
                '"operation":"monitor"}'
            )
        }
        self.actions: list[tuple[DreameMowerAction, object]] = []

    def get_property(self, prop: DreameMowerProperty) -> object:
        return self.data.get(prop.value)

    def call_action(
        self,
        action: DreameMowerAction,
        parameters: object = None,
    ) -> dict[str, object]:
        self.actions.append((action, parameters))
        return {"code": 0, "result": {"url": "https://example.invalid/photo.jpg"}}

    def _handle_properties(self, properties: list[dict[str, object]]) -> bool:
        for item in properties:
            did = item.get("did")
            if did is not None:
                self.data[int(str(did))] = item.get("value")
        return True


class _FakeProtocol:
    def get_properties(self, requested: object) -> list[dict[str, object]]:
        return [
            {
                "did": str(DreameMowerProperty.STREAM_STATUS.value),
                "code": 0,
                "value": (
                    '{"result":0,"session":"session-2","operType":"start",'
                    '"operation":"monitor"}'
                ),
            }
        ]


class _NoResponseCameraDevice(_FakeCameraDevice):
    def call_action(
        self,
        action: DreameMowerAction,
        parameters: object = None,
    ) -> None:
        self.actions.append((action, parameters))
        return None


def _client_with_device(device: object) -> DreameLawnMowerClient:
    client = DreameLawnMowerClient(
        username="user@example.com",
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
    client._device = device
    return client


def test_camera_feature_support_reports_protocol_and_cloud_metadata() -> None:
    client = _client_with_device(_FakeCameraDevice())

    support = client._sync_get_camera_feature_support(include_cloud=False)

    assert isinstance(support, DreameLawnMowerCameraFeatureSupport)
    assert support.supported is True
    assert support.advertised is True
    assert support.ai_detection is True
    assert support.permit == "pincode,video,aiobs"
    assert support.feature == "video_tx"
    assert support.stream_session_present is True
    assert support.stream_status == "video"
    assert support.property_mappings["take_photo"] == {"siid": 10001, "piid": 5}
    assert support.action_mappings["get_photo_info"] == {"siid": 4, "aiid": 6}
    assert support.as_dict()["supported"] is True


def test_camera_feature_support_explains_missing_advertisement() -> None:
    device = _FakeCameraDevice()
    device.capability = SimpleNamespace(
        camera_streaming=False,
        fill_light=None,
        ai_detection=False,
        obstacles=False,
    )
    device.info = SimpleNamespace(raw={"deviceInfo": {}})
    client = _client_with_device(device)

    support = client._sync_get_camera_feature_support(include_cloud=False)

    assert support.supported is False
    assert support.advertised is False
    assert support.reason == (
        "Cloud/device metadata does not advertise camera or photo support."
    )


def test_photo_info_request_delegates_to_get_photo_info_action() -> None:
    device = _FakeCameraDevice()
    client = _client_with_device(device)

    result = client._sync_request_photo_info()

    assert result == {
        "code": 0,
        "result": {"url": "https://example.invalid/photo.jpg"},
    }
    assert device.actions == [(DreameMowerAction.GET_PHOTO_INFO, None)]


def test_camera_device_property_probe_handles_empty_protocol_response() -> None:
    device = _FakeCameraDevice()
    device._protocol = SimpleNamespace(get_properties=lambda requested: None)
    client = _client_with_device(device)

    result = client._sync_probe_camera_device_properties()

    assert result["requested_property_count"] == 10
    assert result["raw_response"] is None
    assert result["handled"] is False
    assert result["error"] == "Device protocol returned no property response."
    assert "stream_status" in result["values"]


def test_camera_sources_probe_builds_payload() -> None:
    device = _FakeCameraDevice()
    device._protocol = _FakeProtocol()
    client = _client_with_device(device)
    client._sync_update_device = lambda: device
    client._sync_scan_cloud_properties = lambda **kwargs: {
        "requested_key_count": 1,
        "returned_entry_count": 1,
        "displayed_entry_count": 1,
        "entries": [{"key": "10001.1"}],
    }
    client._sync_get_cloud_user_features = lambda language=None: ""

    payload = client._sync_probe_camera_sources(
        language="en",
        request_device_properties=True,
    )

    assert payload["descriptor"]["model"] == "dreame.mower.g2408"
    assert payload["support"]["supported"] is True
    assert payload["cloud_property_summary"]["requested_key_count"] == 1
    assert payload["device_properties"]["requested_property_count"] == 10


def test_camera_probe_payload_contains_support_and_property_summary() -> None:
    support = _client_with_device(
        _FakeCameraDevice()
    )._sync_get_camera_feature_support(include_cloud=False)

    payload = build_camera_probe_payload(
        descriptor=_client_with_device(_FakeCameraDevice()).descriptor,
        support=support,
        cloud_properties={"entries": [{"key": "10001.1", "value": "x"}]},
        device_properties={"error": None},
    )

    assert payload["support"]["supported"] is True
    assert payload["cloud_property_summary"]["non_empty_keys"] == ["10001.1"]
    assert payload["device_properties"] == {"error": None}


def test_photo_info_request_raises_on_empty_device_response() -> None:
    device = _NoResponseCameraDevice()
    client = _client_with_device(device)

    try:
        client._sync_request_photo_info()
    except DreameLawnMowerConnectionError as err:
        assert str(err) == "GET_PHOTO_INFO returned no response."
    else:
        raise AssertionError("Expected no-response photo info request to fail")
