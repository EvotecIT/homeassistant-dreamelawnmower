"""Regression checks for mower-native app schedule retrieval."""

from __future__ import annotations

import base64
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
)
from dreame_lawn_mower_client._loader import load_internal_module
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor
from dreame_lawn_mower_client.schedule import decode_schedule_week_payload

DreameMowerDevice = load_internal_module("device").DreameMowerDevice
DreameMowerProperty = load_internal_module("device_types").DreameMowerProperty
DreameMowerPropertyMapping = load_internal_module(
    "device_types"
).DreameMowerPropertyMapping


class _PropertyReadDeviceStub(SimpleNamespace):
    """Retain the real property plan when isolating schedule transport."""

    _request_properties_plan = DreameMowerDevice._request_properties_plan



class _FakeAppScheduleCloud:
    logged_in = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.request_options: list[dict[str, object]] = []
        self._pending_uploads: dict[int, dict[str, object]] = {}
        self.payloads = {
            -1: {"version": 31345, "text": '{"d":[[0,1,"","EODBJwAAADDgwScAAAA="]]}'},
            0: {
                "version": 19383,
                "text": ('{"d":[[0,1,"","AJKSTiIDABCSkk7/DwA="],[1,0,""]]}'),
            },
            1: {"version": 4760, "text": '{"d":[[0,0,""]]}'},
        }

    def call_app_action(
        self,
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
        retry_count: int | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> dict[str, object]:
        assert siid == 2
        assert aiid == 50
        self.calls.append(payload)
        self.request_options.append(
            {
                "command": payload.get("t"),
                "retry_count": retry_count,
                "timeout": timeout,
                "deadline": deadline,
            }
        )
        command = payload.get("t")
        if command == "MAPL":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": [[0, 1, 1, 1, 0], [1, 0, 1, 1, 0]],
                    }
                ]
            }
        if command == "SCHDT":
            return {"out": [{"m": "r", "r": 0, "d": [658, 1257, 0, 19383]}]}
        if command == "SCHDIV2":
            if payload.get("m") == "s":
                data = payload["d"]
                idx = int(data["i"])
                version = int(data["v"])
                length = int(data["l"])
                self._pending_uploads[version] = {
                    "idx": idx,
                    "length": length,
                    "chunks": [],
                }
                return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "v": version}}]}
            idx = int(payload["d"]["i"])
            entry = self.payloads[idx]
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {
                            "i": idx,
                            "l": len(entry["text"].encode("utf-8")),
                            "v": entry["version"],
                        },
                    }
                ]
            }
        if command == "SCHDDV2":
            data = payload["d"]
            version = int(data["v"])
            if payload.get("m") == "s":
                upload = self._pending_uploads[version]
                upload["chunks"].append(str(data["d"]))
                combined = "".join(upload["chunks"])
                if len(combined.encode("utf-8")) >= int(upload["length"]):
                    idx = int(upload["idx"])
                    self.payloads[idx] = {
                        "version": version,
                        "text": combined,
                    }
                return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "v": version}}]}
            start = int(data["s"])
            size = int(data["l"])
            text = next(
                item["text"]
                for item in self.payloads.values()
                if item["version"] == version
            )
            chunk = text.encode("utf-8")[start : start + size].decode("utf-8")
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {"l": len(chunk.encode("utf-8")), "d": chunk},
                    }
                ]
            }
        if command == "SCHDSV2":
            return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "v": 19383}}]}
        raise AssertionError(f"Unexpected app command: {payload}")


def _client() -> DreameLawnMowerClient:
    client = DreameLawnMowerClient(
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
    client._sync_update_device = Mock(
        return_value=SimpleNamespace(
            available=True,
            activity="docked",
            state="idle",
            mowing_session_active=False,
            task_resumable=False,
        )
    )
    client._snapshot_from_device = lambda device, **kwargs: device
    return client


@pytest.mark.parametrize("upload", [False, True])
@pytest.mark.parametrize(
    "state_changes",
    [
        {"task_resumable": True, "mowing_session_active": True, "state": "paused"},
        {"activity": "mowing", "mowing_session_active": True},
        {"task_resumable": None},
        {"mowing_session_active": None},
    ],
)
def test_sync_schedule_writes_require_a_fresh_finished_task(upload, state_changes):
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    client._latest_snapshot = SimpleNamespace(task_resumable=False)
    vars(client._sync_update_device.return_value).update(state_changes)
    plans = decode_schedule_payload_text(cloud.payloads[0]["text"])
    call = (
        (
            lambda: client._sync_plan_app_schedule_upload(
                map_index=0, plans=plans, execute=True, confirm_write=True
            )
        )
        if upload
        else lambda: client._sync_set_app_schedule_plan_enabled(
            map_index=0, plan_id=1, enabled=False, execute=True, confirm_write=True
        )
    )
    with pytest.raises(DreameLawnMowerCommandRejectedError, match="task"):
        call()
    client._sync_update_device.assert_called_once_with(force_request_properties=True)
    assert all(request["m"] == "g" for request in cloud.calls)


@pytest.mark.parametrize("upload", [False, True])
def test_sync_schedule_preview_does_not_require_finished_task(upload):
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    client._sync_update_device.side_effect = AssertionError("Preview read task state")
    plans = decode_schedule_payload_text(cloud.payloads[0]["text"])
    call = (
        (lambda: client._sync_plan_app_schedule_upload(map_index=0, plans=plans))
        if upload
        else lambda: client._sync_set_app_schedule_plan_enabled(
            map_index=0, plan_id=1, enabled=False
        )
    )
    assert call()["executed"] is False
    assert all(request["m"] == "g" for request in cloud.calls)


def test_fresh_task_read_failure_prevents_schedule_dispatch():
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    client._sync_update_device.side_effect = DreameLawnMowerConnectionError("Offline")
    with pytest.raises(DreameLawnMowerConnectionError, match="Offline"):
        (
            lambda: client._sync_set_app_schedule_plan_enabled(
                map_index=0, plan_id=1, enabled=True, execute=True, confirm_write=True
            )
        )()
    assert all(request["m"] == "g" for request in cloud.calls)


@pytest.mark.parametrize("upload", [False, True])
@pytest.mark.parametrize(
    "failure", ["none", "empty", "missing", "rejected", "duplicate"]
)
def test_failed_property_read_cannot_reuse_cached_idle_for_schedule_write(
    upload, failure
):
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    required = [
        DreameMowerProperty.STATE,
        DreameMowerProperty.STATUS,
        DreameMowerProperty.TASK_STATUS,
        DreameMowerProperty.CLEANING_PAUSED,
    ]
    rows = [
        {
            "did": str(prop.value),
            **DreameMowerPropertyMapping[prop],
            "code": 0,
            "value": 0,
        }
        for prop in required
    ]
    if failure == "none":
        rows = None
    elif failure == "empty":
        rows = []
    elif failure == "missing":
        rows.pop()
    elif failure == "rejected":
        rows[-1]["code"] = -1
    elif failure == "duplicate":
        rows.append(dict(rows[-1]))
    cached_data = {prop.value: 0 for prop in required}
    device = _PropertyReadDeviceStub(
        _update_running=False,
        _state_lock=RLock(),
        _update_interval=10,
        available=True,
        cloud_connected=True,
        device_connected=True,
        capability=SimpleNamespace(backup_map=False),
        status=SimpleNamespace(active=False),
        _consumable_change=False,
        _last_settings_request=10**20,
        _map_manager=None,
        _ready=True,
        data=cached_data,
        property_mapping=DreameMowerPropertyMapping,
        _protocol=SimpleNamespace(
            dreame_cloud=True, get_properties=Mock(return_value=rows)
        ),
        _handle_properties=Mock(),
    )
    device._request_properties = lambda *args, **kwargs: (
        DreameMowerDevice._request_properties(device, *args, **kwargs)
    )
    device._select_update_properties = lambda: (
        DreameMowerDevice._select_update_properties(device)
    )
    device.update = lambda **kwargs: DreameMowerDevice.update(device, **kwargs)
    client._ensure_device = lambda: device
    client._sync_update_device = lambda **kwargs: (
        DreameLawnMowerClient._sync_update_device(client, **kwargs)
    )
    client._snapshot_from_device = Mock(
        return_value=SimpleNamespace(
            available=True,
            activity="docked",
            state="idle",
            mowing_session_active=False,
            task_resumable=False,
        )
    )
    plans = decode_schedule_payload_text(cloud.payloads[0]["text"])
    call = (
        (
            lambda: client._sync_plan_app_schedule_upload(
                map_index=0, plans=plans, execute=True, confirm_write=True
            )
        )
        if upload
        else lambda: client._sync_set_app_schedule_plan_enabled(
            map_index=0, plan_id=1, enabled=True, execute=True, confirm_write=True
        )
    )
    with pytest.raises(
        DreameLawnMowerConnectionError, match="Fresh mower task properties"
    ):
        call()
    client._snapshot_from_device.assert_not_called()
    device._handle_properties.assert_not_called()
    assert device.data is cached_data and device.available is True
    assert all(request["m"] == "g" for request in cloud.calls)


def test_unchanged_fresh_properties_accept_unknown_optional_fields():
    required = [
        DreameMowerProperty.STATE,
        DreameMowerProperty.STATUS,
        DreameMowerProperty.TASK_STATUS,
        DreameMowerProperty.CLEANING_PAUSED,
    ]
    rows = [
        {
            "did": str(prop.value),
            **DreameMowerPropertyMapping[prop],
            "code": 0,
            "value": 0,
        }
        for prop in required
    ]
    rows.append({"did": "9999", "code": -1})
    device = _PropertyReadDeviceStub(
        _ready=True,
        data={},
        _state_lock=RLock(),
        property_mapping=DreameMowerPropertyMapping,
        _protocol=SimpleNamespace(get_properties=Mock(return_value=rows)),
        _handle_properties=Mock(return_value=False),
    )
    assert (
        DreameMowerDevice._request_properties(
            device, required, require_fresh_state=True
        )
        is False
    )
    device._handle_properties.assert_called_once_with(rows)
    assert len(device._protocol.get_properties.call_args.args[0]) == 5


@pytest.mark.parametrize("failure", [None, "bad_frame", "state_rejected"])
def test_native_heartbeat_is_fresh_task_evidence_without_legacy_properties(failure):
    # Supported current firmware heartbeat, with known idle task/docking fields.
    frame = [
        206,
        0,
        0,
        0,
        0,
        0,
        0,
        5,
        0,
        0,
        0,
        50,
        177,
        255,
        0,
        0,
        128,
        200,
        186,
        0,
        128,
        206,
    ]
    if failure == "bad_frame":
        frame[-1] = 0
    rows = [
        {
            "did": str(DreameMowerProperty.STATE.value),
            "siid": 2,
            "piid": 1,
            "code": -1 if failure == "state_rejected" else 0,
            "value": 1,
        },
        {"did": "100001", "siid": 1, "piid": 1, "code": 0, "value": frame},
    ]
    retained = {"value": [206, 0, 206], "received_at": 1.0}
    device = _PropertyReadDeviceStub(
        _ready=True,
        data={},
        _state_lock=RLock(),
        property_mapping=DreameMowerPropertyMapping,
        realtime_properties={"1.1": retained},
        _protocol=SimpleNamespace(get_properties=Mock(return_value=rows)),
        _handle_properties=Mock(return_value=False),
    )
    if failure:
        error_type = load_internal_module("exceptions").DeviceUpdateFailedException
        with pytest.raises(error_type, match="incomplete or rejected"):
            DreameMowerDevice._request_properties(
                device, [DreameMowerProperty.STATE], require_fresh_state=True
            )
        assert device.realtime_properties["1.1"] is retained
        device._handle_properties.assert_not_called()
    else:
        assert (
            DreameMowerDevice._request_properties(
                device, [DreameMowerProperty.STATE], require_fresh_state=True
            )
            is False
        )
        decoded = load_internal_module("client_core")._decoded_realtime_status_blob(
            device, "1.1"
        )
        assert (
            decoded.mowing_session_active is False and decoded.task_resumable is False
        )
        assert device.realtime_properties["1.1"]["received_at"] > 1.0
        device._handle_properties.assert_called_once_with(rows)


def test_app_schedules_decode_plans_and_current_task() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_get_app_schedules(chunk_size=40)

    assert result["available"] is True
    assert result["current_task"] == {
        "start": 658,
        "start_time": "10:58",
        "end": 1257,
        "end_time": "20:57",
        "plan_id": 0,
        "version": 19383,
    }
    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 0, 1]
    map_0 = result["schedules"][1]
    assert map_0["available"] is True
    assert map_0["version"] == 19383
    assert map_0["plan_count"] == 2
    assert map_0["enabled_plan_count"] == 1
    assert map_0["plans"][0]["weeks"][0] == {
        "week_day": 0,
        "week_day_name": "sun",
        "tasks": [
            {
                "type": 0,
                "type_name": "all_area_mowing",
                "cyclic": False,
                "start": 658,
                "start_time": "10:58",
                "end": 1257,
                "end_time": "20:57",
                "real_end": 802,
                "real_end_time": "13:22",
                "regions": [],
            }
        ],
    }
    assert "raw_text" not in map_0
    assert [call["t"] for call in cloud.calls[:3]] == ["SCHDT", "MAPL", "SCHDIV2"]
    assert cloud.request_options[0]["command"] == "SCHDT"
    assert cloud.request_options[0]["retry_count"] == 0
    assert 0 < cloud.request_options[0]["timeout"] <= 5.0
    assert isinstance(cloud.request_options[0]["deadline"], float)
    assert cloud.request_options[0]["deadline"] > 0
    assert {
        key: value
        for key, value in cloud.request_options[1].items()
        if key not in {"deadline", "timeout"}
    } == {
        "command": "MAPL",
        "retry_count": 0,
    }
    assert 0 < cloud.request_options[1]["timeout"] <= 2.5
    assert isinstance(cloud.request_options[1]["deadline"], float)
    assert cloud.request_options[2]["retry_count"] == 0
    assert 0 < cloud.request_options[2]["timeout"] <= 5.0


def test_app_schedules_can_include_raw_payload_text() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_get_app_schedules(
        include_raw=True,
        map_indices=[0],
        chunk_size=100,
    )

    assert [schedule["idx"] for schedule in result["schedules"]] == [0]
    assert result["schedules"][0]["raw_text"].startswith('{"d":')
    assert [call["t"] for call in cloud.calls] == ["SCHDT", "SCHDIV2", "SCHDDV2"]


def test_app_schedules_can_skip_optional_current_task_and_bound_plan_reads() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_get_app_schedules(
        map_indices=[0],
        include_current_task=False,
    )

    assert result["available"] is True
    assert result["current_task"] is None
    assert [call["t"] for call in cloud.calls] == ["SCHDIV2", "SCHDDV2"]
    assert all(option["retry_count"] == 0 for option in cloud.request_options)
    assert all(0 < option["timeout"] <= 5.0 for option in cloud.request_options)
    # Protocol discovery reserves alternate-protocol time for metadata, while
    # a validated document can use the remaining slot for its payload.
    assert cloud.request_options[0]["deadline"] < cloud.request_options[1]["deadline"]


def test_app_schedules_allocate_shared_deadline_fairly_across_slots() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_get_app_schedules(
        map_indices=[-1, 0, 1],
        include_current_task=False,
    )

    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 0, 1]
    payload_deadlines = [
        options["deadline"]
        for call, options in zip(
            cloud.calls,
            cloud.request_options,
            strict=True,
        )
        if call["t"] == "SCHDDV2"
    ]
    assert len(payload_deadlines) == 3
    assert payload_deadlines[0] < payload_deadlines[1] < payload_deadlines[2]


def test_app_schedules_reserve_slot_time_when_map_discovery_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]

    class _SlowMapCloud(_FakeAppScheduleCloud):
        def call_app_action(
            self,
            payload: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object]:
            if payload.get("t") == "MAPL":
                self.calls.append(payload)
                self.request_options.append(
                    {
                        "command": "MAPL",
                        "retry_count": kwargs.get("retry_count"),
                        "timeout": kwargs.get("timeout"),
                        "deadline": kwargs.get("deadline"),
                    }
                )
                clock[0] = float(kwargs["deadline"])
                raise TimeoutError("MAPL timed out")
            return super().call_app_action(payload, **kwargs)

    cloud = _SlowMapCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    settings_time = client._sync_get_app_schedules.__func__.__globals__["time"]
    monkeypatch.setattr(settings_time, "monotonic", lambda: clock[0])

    result = client._sync_get_app_schedules(include_current_task=False)

    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 0, 1]
    map_deadline = cloud.request_options[0]["deadline"]
    first_schedule_deadline = cloud.request_options[1]["deadline"]
    assert map_deadline == pytest.approx(102.5)
    assert 102.5 < first_schedule_deadline <= 103.75


def test_app_schedules_fall_back_to_likely_slots_when_map_list_is_missing() -> None:
    client = _client()

    class _MissingMapListCloud(_FakeAppScheduleCloud):
        def call_app_action(
            self,
            payload: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object] | None:
            if payload.get("t") == "MAPL":
                self.calls.append(payload)
                self.request_options.append(
                    {
                        "command": "MAPL",
                        "retry_count": kwargs.get("retry_count"),
                        "timeout": kwargs.get("timeout"),
                        "deadline": kwargs.get("deadline"),
                    }
                )
                return None
            return super().call_app_action(payload, **kwargs)

    cloud = _MissingMapListCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_get_app_schedules(include_current_task=False)

    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 0, 1]


def test_app_schedules_retry_early_slot_with_unused_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    default_attempts = 0

    class _RecoveringSlotCloud(_FakeAppScheduleCloud):
        def call_app_action(
            self,
            payload: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object]:
            nonlocal default_attempts
            if payload.get("t") == "SCHDIV2" and payload["d"]["i"] == -1:
                default_attempts += 1
                if default_attempts == 1:
                    self.calls.append(payload)
                    self.request_options.append(
                        {
                            "command": "SCHDIV2",
                            "retry_count": kwargs.get("retry_count"),
                            "timeout": kwargs.get("timeout"),
                            "deadline": kwargs.get("deadline"),
                        }
                    )
                    clock[0] = float(kwargs["deadline"])
                    raise TimeoutError("initial fair share expired")
            return super().call_app_action(payload, **kwargs)

    cloud = _RecoveringSlotCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    settings_time = client._sync_get_app_schedules.__func__.__globals__["time"]
    monkeypatch.setattr(settings_time, "monotonic", lambda: clock[0])

    result = client._sync_get_app_schedules(
        map_indices=[-1, 0, 1],
        include_current_task=False,
    )

    assert result["errors"] == []
    assert result["schedules"][0]["version"] == 31345
    default_deadlines = [
        option["deadline"]
        for call, option in zip(cloud.calls, cloud.request_options, strict=True)
        if call["t"] == "SCHDIV2" and call["d"]["i"] == -1
    ]
    assert default_deadlines[0] < default_deadlines[1]
    assert default_deadlines[1] >= default_deadlines[0] + 3
    assert (
        max(
            option["deadline"]
            for option in cloud.request_options
            if option["deadline"] is not None
        )
        <= 110.0
    )


def test_app_schedules_rotate_reserved_recovery_across_slow_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]

    class _MultipleSlowSlotsCloud(_FakeAppScheduleCloud):
        def call_app_action(
            self,
            payload: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object]:
            command = payload.get("t")
            if command == "MAPL":
                self.calls.append(payload)
                self.request_options.append(
                    {
                        "command": "MAPL",
                        "retry_count": kwargs.get("retry_count"),
                        "timeout": kwargs.get("timeout"),
                        "deadline": kwargs.get("deadline"),
                    }
                )
                clock[0] = float(kwargs["deadline"])
                raise TimeoutError("MAPL timed out")
            if command == "SCHDIV2" and payload["d"]["i"] in {-1, 0}:
                deadline = float(kwargs["deadline"])
                if deadline - clock[0] < 4.0:
                    self.calls.append(payload)
                    self.request_options.append(
                        {
                            "command": "SCHDIV2",
                            "retry_count": kwargs.get("retry_count"),
                            "timeout": kwargs.get("timeout"),
                            "deadline": kwargs.get("deadline"),
                        }
                    )
                    clock[0] = deadline
                    raise TimeoutError("slot needs four seconds")
                clock[0] += 4.0
            return super().call_app_action(payload, **kwargs)

    cloud = _MultipleSlowSlotsCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    settings_time = client._sync_get_app_schedules.__func__.__globals__["time"]
    monkeypatch.setattr(settings_time, "monotonic", lambda: clock[0])

    first = client._sync_get_app_schedules(include_current_task=False)
    second = client._sync_get_app_schedules(include_current_task=False)

    assert first["schedules"][0]["version"] == 31345
    assert first["schedules"][1]["available"] is False
    assert second["schedules"][0]["available"] is False
    assert second["schedules"][1]["version"] == 19383
    assert client._app_schedule_retry_offset == 0


@pytest.mark.parametrize(
    "payload_text",
    [
        '{"d":[[0,1,"","EODBJwAAADDgwScAAAA="]]}',
        (
            '{"d":[[0,1,"","AJKSTiIDABCSkk7/DwAgkpJO/w8AMJKSTv8PAECSkk7/'
            'DwBQkpJOAAAAYJKSTv8PAA=="],[1,0,""]]}'
        ),
        '{"d":[[0,0,""]]}',
    ],
)
def test_schedule_payload_round_trips(payload_text: str) -> None:
    plans = decode_schedule_payload_text(payload_text)

    assert encode_schedule_payload_text(plans) == payload_text


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("!!!!!", "base64"),
        (base64.b64encode(bytes.fromhex("10000000ff0f001000")).decode(), "header"),
        (base64.b64encode(bytes.fromhex("70000000ff0f00")).decode(), "weekday"),
        (base64.b64encode(bytes.fromhex("10a00500ff0f00")).decode(), "start"),
        (base64.b64encode(bytes.fromhex("1000005aff0f00")).decode(), "end"),
        (base64.b64encode(bytes.fromhex("10000000ff1f00")).decode(), "regions"),
        (base64.b64encode(bytes.fromhex("12000000ff1f0001")).decode(), "pairs"),
    ],
)
def test_schedule_task_payload_rejects_unreadable_records(
    payload: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        decode_schedule_week_payload(payload)


def test_schedule_upload_rejects_non_weekly_day() -> None:
    with pytest.raises(ValueError, match="week_day"):
        encode_schedule_payload_text(
            [{"plan_id": 0, "enabled": True, "weeks": [{"week_day": 7, "tasks": []}]}]
        )


def test_schedule_upload_requests_chunk_payload() -> None:
    requests = build_schedule_upload_requests(
        map_index=0,
        payload_text='{"d":[]}',
        version=123,
        chunk_size=4,
    )

    assert requests == [
        {"m": "s", "t": "SCHDIV2", "d": {"i": 0, "l": 8, "v": 123}},
        {"m": "s", "t": "SCHDDV2", "d": {"s": 0, "l": 4, "d": '{"d"', "v": 123}},
        {"m": "s", "t": "SCHDDV2", "d": {"s": 4, "l": 4, "d": ":[]}", "v": 123}},
    ]


def test_schedule_upload_requests_do_not_split_utf8_characters() -> None:
    requests = build_schedule_upload_requests(
        map_index=0,
        payload_text='{"name":"zażółć"}',
        version=123,
        chunk_size=10,
    )

    chunks = [request["d"] for request in requests[1:]]
    assert "".join(chunk["d"] for chunk in chunks) == '{"name":"zażółć"}'
    assert [chunk["s"] for chunk in chunks] == [0, 10, 20]
    assert [chunk["l"] for chunk in chunks] == [10, 10, 1]


def test_set_app_schedule_plan_enabled_builds_dry_run_request() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_set_app_schedule_plan_enabled(
        map_index=0,
        plan_id=1,
        enabled=True,
    )

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["previous_enabled"] is False
    assert result["enabled"] is True
    assert result["changed"] is True
    assert result["schedule"] == {
        "idx": 0,
        "label": "map_0",
        "available": True,
        "version": 19383,
        "plan_count": 2,
        "enabled_plan_count": 1,
    }
    assert result["target_plan"] == {
        "plan_id": 1,
        "name": "",
        "previous_enabled": False,
        "enabled": True,
        "week_count": 0,
        "task_count": 0,
        "first_start_time": None,
        "first_end_time": None,
        "type_names": [],
    }
    assert result["request"] == {
        "m": "s",
        "t": "SCHDSV2",
        "d": {"i": 0, "v": 19383, "s": [1, 1]},
    }
    assert [call["t"] for call in cloud.calls] == ["SCHDIV2", "SCHDDV2"]


def test_set_app_schedule_plan_enabled_requires_confirmation_to_execute() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    with pytest.raises(ValueError, match="confirm_write=True"):
        client._sync_set_app_schedule_plan_enabled(
            map_index=0,
            plan_id=1,
            enabled=True,
            execute=True,
        )


def test_set_app_schedule_plan_enabled_can_execute_when_confirmed() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_set_app_schedule_plan_enabled(
        map_index=0,
        plan_id=1,
        enabled=True,
        execute=True,
        confirm_write=True,
    )

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["changed"] is True
    assert result["response"] == {"m": "r", "r": 0, "d": {"r": 0, "v": 19383}}
    assert result["response_data"] == {"r": 0, "v": 19383}
    assert [call["t"] for call in cloud.calls] == [
        "SCHDIV2",
        "SCHDDV2",
        "SCHDSV2",
    ]


def test_schedule_status_result_reports_the_acknowledged_document_version() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    normal_call = client._sync_call_app_action

    def changed_version(payload: dict[str, object], **kwargs: object) -> object:
        if payload["t"] == "SCHDSV2":
            return {"m": "r", "r": 0, "d": {"r": 0, "v": 62089}}
        return normal_call(payload, **kwargs)

    client._sync_call_app_action = changed_version
    result = client._sync_set_app_schedule_plan_enabled(
        map_index=0, plan_id=1, enabled=True, execute=True, confirm_write=True
    )

    assert result["request"]["d"]["v"] == 19383
    assert result["schedule"]["version"] == 19383
    assert result["version"] == 62089
    assert result["acknowledged_plan_states"] == [
        {"plan_id": 0, "enabled": True},
        {"plan_id": 1, "enabled": True},
    ]


def test_set_app_schedule_plan_enabled_rejects_failed_write_response() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    def failing_call(
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        if payload["t"] == "SCHDSV2":
            return {"m": "r", "r": 0, "d": {"r": 1, "v": 19383}}
        return cloud.call_app_action(payload)["out"][0]

    client._sync_call_app_action = failing_call

    with pytest.raises(DreameLawnMowerConnectionError, match="Schedule write failed"):
        client._sync_set_app_schedule_plan_enabled(
            map_index=0,
            plan_id=1,
            enabled=True,
            execute=True,
            confirm_write=True,
        )


def test_set_app_schedule_plan_enabled_rejects_lost_acknowledgement() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    normal_call = client._sync_call_app_action

    def lost_ack(payload: dict[str, object], **_kwargs: object) -> object:
        if payload["t"] == "SCHDSV2":
            return None
        return normal_call(payload)

    client._sync_call_app_action = lost_ack

    with pytest.raises(
        DreameLawnMowerConnectionError,
        match="did not acknowledge",
    ):
        client._sync_set_app_schedule_plan_enabled(
            map_index=0,
            plan_id=1,
            enabled=True,
            execute=True,
            confirm_write=True,
        )


def test_plan_app_schedule_upload_builds_dry_run_sequence() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_plan_app_schedule_upload(
        map_index=0,
        plans=[
            {
                "plan_id": 0,
                "enabled": True,
                "name": "",
                "weeks": [
                    {
                        "week_day": 0,
                        "tasks": [
                            {
                                "type": 0,
                                "start": 658,
                                "end": 1257,
                                "real_end": 802,
                                "regions": [],
                            }
                        ],
                    }
                ],
            },
            {"plan_id": 1, "enabled": True, "name": ""},
        ],
    )

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["changed"] is True
    assert result["schedule"] == {
        "idx": 0,
        "label": "map_0",
        "available": True,
        "version": 19383,
        "plan_count": 2,
        "enabled_plan_count": 1,
    }
    assert result["target_schedule"] == {
        "plan_count": 2,
        "enabled_plan_count": 2,
        "week_count": 1,
        "task_count": 1,
        "plan_ids": [0, 1],
    }
    assert result["chunk_count"] == 1
    assert result["request"] == {
        "sequence": [
            {"m": "s", "t": "SCHDIV2", "d": {"i": 0, "l": 40, "v": 19383}},
            {
                "m": "s",
                "t": "SCHDDV2",
                "d": {
                    "s": 0,
                    "l": 40,
                    "d": '{"d":[[0,1,"","AJKSTiIDAA=="],[1,1,""]]}',
                    "v": 19383,
                },
            },
        ]
    }
    assert [call["t"] for call in cloud.calls] == ["SCHDIV2", "SCHDDV2"]


def test_plan_app_schedule_upload_requires_confirmation_to_execute() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    with pytest.raises(ValueError, match="confirm_write=True"):
        client._sync_plan_app_schedule_upload(
            map_index=0,
            plans=[{"plan_id": 0, "enabled": True, "name": ""}],
            execute=True,
        )


def test_plan_app_schedule_upload_can_execute_when_confirmed() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud

    result = client._sync_plan_app_schedule_upload(
        map_index=0,
        plans=[{"plan_id": 0, "enabled": False, "name": ""}],
        execute=True,
        confirm_write=True,
    )

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["changed"] is True
    assert result["response_data"] == [
        {"r": 0, "v": 19383},
        {"r": 0, "v": 19383},
    ]
    assert cloud.payloads[0]["text"] == '{"d":[[0,0,""]]}'
    assert [call["t"] for call in cloud.calls] == [
        "SCHDIV2",
        "SCHDDV2",
        "SCHDIV2",
        "SCHDDV2",
    ]
