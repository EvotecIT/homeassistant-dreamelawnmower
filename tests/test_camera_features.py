"""Regression checks for read-only camera/photo feature discovery."""

from __future__ import annotations

from types import SimpleNamespace

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
            DreameMowerProperty.TAKE_PHOTO: {"siid": 10001, "piid": 5},
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


def test_photo_info_request_raises_on_empty_device_response() -> None:
    device = _NoResponseCameraDevice()
    client = _client_with_device(device)

    try:
        client._sync_request_photo_info()
    except DreameLawnMowerConnectionError as err:
        assert str(err) == "GET_PHOTO_INFO returned no response."
    else:
        raise AssertionError("Expected no-response photo info request to fail")
