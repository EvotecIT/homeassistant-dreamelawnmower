"""Regression checks for reusable remote-control support helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_client.types import (
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStatus,
)
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerRemoteControlSupport,
)


class _FakeRemoteControlDevice:
    def __init__(self) -> None:
        self.property_mapping = {
            DreameMowerProperty.REMOTE_CONTROL: {"siid": 4, "piid": 15}
        }
        self.status = SimpleNamespace(
            state=DreameMowerState.CHARGING,
            status=DreameMowerStatus.CHARGING,
            fast_mapping=False,
        )
        self._remote_control = False
        self.commands: list[dict[str, object]] = []

    def remote_control_move_step(
        self,
        *,
        rotation: int = 0,
        velocity: int = 0,
        prompt: bool | None = None,
    ) -> dict[str, object]:
        self._remote_control = True
        command = {
            "rotation": rotation,
            "velocity": velocity,
            "prompt": prompt,
        }
        self.commands.append(command)
        return {"code": 0, "command": command}


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


def test_remote_control_support_reports_protocol_mapping() -> None:
    client = _client_with_device(_FakeRemoteControlDevice())

    support = client._sync_get_remote_control_support()

    assert isinstance(support, DreameLawnMowerRemoteControlSupport)
    assert support.supported is True
    assert support.active is False
    assert support.siid == 4
    assert support.piid == 15
    assert support.state == "charging"
    assert support.status == "charging"
    assert support.as_dict()["supported"] is True


def test_remote_control_move_step_delegates_to_protocol_device() -> None:
    device = _FakeRemoteControlDevice()
    client = _client_with_device(device)

    result = client._sync_remote_control_move_step(
        rotation=120,
        velocity=240,
        prompt=False,
    )

    assert result == {
        "code": 0,
        "command": {"rotation": 120, "velocity": 240, "prompt": False},
    }
    assert device.commands == [
        {"rotation": 120, "velocity": 240, "prompt": False}
    ]
    assert client._sync_get_remote_control_support().active is True


def test_remote_control_support_blocks_fast_mapping() -> None:
    device = _FakeRemoteControlDevice()
    device.status.fast_mapping = True
    client = _client_with_device(device)

    support = client._sync_get_remote_control_support()

    assert support.supported is False
    assert support.siid == 4
    assert support.piid == 15
    assert support.reason == "Remote control is blocked while fast mapping."


def test_remote_control_move_step_rejects_missing_mapping() -> None:
    device = _FakeRemoteControlDevice()
    device.property_mapping = {}
    client = _client_with_device(device)

    with pytest.raises(
        DreameLawnMowerConnectionError,
        match="Remote-control property mapping is not available",
    ):
        client._sync_remote_control_move_step(rotation=0, velocity=0, prompt=False)


def test_remote_control_step_validation_rejects_unsafe_values() -> None:
    client = _client_with_device(_FakeRemoteControlDevice())

    with pytest.raises(ValueError, match="rotation must be an integer"):
        client._sync_remote_control_move_step(rotation=True, velocity=0, prompt=False)

    with pytest.raises(ValueError, match="velocity must be between"):
        client._sync_remote_control_move_step(rotation=0, velocity=1001, prompt=False)
