"""Fresh property evidence required before state-changing command decisions."""

import copy
import time
from collections.abc import Generator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypedDict

from .app_protocol import MOWER_RAW_STATUS_PROPERTY_KEY, decode_mower_status_blob
from .device_types import DreameMowerProperty
from .exceptions import DeviceUpdateFailedException

if TYPE_CHECKING:
    from .device import DreameMowerDevice
    from .device_action_plan import DevicePlanEffect

TASK_DECISION_PROPERTIES = frozenset(
    {
        DreameMowerProperty.STATE,
        DreameMowerProperty.STATUS,
        DreameMowerProperty.TASK_STATUS,
        DreameMowerProperty.CLEANING_PAUSED,
    }
)


def build_device_property_request(
    properties: Sequence[DreameMowerProperty],
    property_mapping: Mapping[DreameMowerProperty, Mapping[str, int]],
    known_properties: Mapping[int, object],
    *,
    ready: bool,
    require_fresh_state: bool,
) -> list[dict[str, int | str]]:
    """Select readable properties and preserve mandatory fresh-task evidence."""
    requests: list[dict[str, int | str]] = []
    for prop in properties:
        mapping = property_mapping.get(prop)
        if (
            mapping is not None
            and "aiid" not in mapping
            and (
                not ready
                or prop.value in known_properties
                or (require_fresh_state and prop in TASK_DECISION_PROPERTIES)
            )
        ):
            requests.append({"did": str(prop.value), **mapping})
    if require_fresh_state:
        # Current firmware carries task evidence in the native heartbeat. A
        # fresh read must request it even when no prior MQTT value was cached.
        requests.append({"did": "100001", "siid": 1, "piid": 1})
    return requests


def apply_device_property_response(
    device: "DreameMowerDevice",
    results: object,
    *,
    require_fresh_state: bool,
) -> bool:
    """Validate fresh task evidence before applying a device property response."""
    if require_fresh_state:
        with device._state_lock:
            _record_fresh_property_evidence(device, results, require_fresh_state=True)
            return device._handle_properties(results)
    return device._handle_properties(results)


def apply_device_property_response_plan(
    device: "DreameMowerDevice",
    results: object,
    *,
    require_fresh_state: bool,
) -> Generator["DevicePlanEffect", Any, bool]:
    """Apply the same validated response through the caller's transport driver.

    The driver owns the state lock for each step, releasing it across I/O.
    """
    _record_fresh_property_evidence(
        device, results, require_fresh_state=require_fresh_state
    )
    return (yield from device._handle_properties_plan(results))


def _record_fresh_property_evidence(
    device: "DreameMowerDevice",
    results: object,
    *,
    require_fresh_state: bool,
) -> None:
    """Record validated decision evidence while the caller owns the state lock."""
    if require_fresh_state:
        evidence = require_fresh_task_properties(results, device.property_mapping)
        observed_at = time.time()
        device._fresh_task_state = {
            **copy.deepcopy(evidence),
            "received_at": observed_at,
        }
        heartbeat = evidence.get("heartbeat")
        if heartbeat is not None:
            device.realtime_properties[MOWER_RAW_STATUS_PROPERTY_KEY] = {
                **copy.deepcopy(heartbeat),
                "received_at": observed_at,
                "last_seen": observed_at,
            }


class _FreshTaskEvidence(TypedDict, total=False):
    """Validated task evidence retained after a successful property read."""

    heartbeat: dict[str, Any]
    legacy_task_status: Any


def _successful_row(rows: Sequence[Mapping[str, Any]]) -> bool:
    return (
        len(rows) == 1 and rows[0].get("code") == 0 and rows[0].get("value") is not None
    )


def require_fresh_task_properties(
    results: object,
    property_mapping: Mapping[DreameMowerProperty, Mapping[str, int]],
) -> _FreshTaskEvidence:
    """Reject missing, failed or ambiguous task properties without changing caches."""
    if not isinstance(results, list | tuple):
        raise DeviceUpdateFailedException(
            "Fresh mower task properties were not returned."
        )
    successful = set()
    for prop in TASK_DECISION_PROPERTIES:
        mapping = property_mapping.get(prop)
        if mapping is None:
            continue
        matches = [
            row
            for row in results
            if isinstance(row, dict)
            and (
                (row.get("siid"), row.get("piid")) == (mapping["siid"], mapping["piid"])
                or (
                    "siid" not in row
                    and "piid" not in row
                    and str(row.get("did")) == str(prop.value)
                )
            )
        ]
        if _successful_row(matches):
            successful.add(prop)
    heartbeat_rows = [
        row
        for row in results
        if isinstance(row, dict) and (row.get("siid"), row.get("piid")) == (1, 1)
    ]
    if _successful_row(heartbeat_rows):
        decoded = decode_mower_status_blob(
            heartbeat_rows[0]["value"],
            source="property_read",
            property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
        )
        if (
            DreameMowerProperty.STATE in successful
            and decoded is not None
            and decoded.mowing_session_active is not None
            and decoded.task_resumable is not None
        ):
            return {"heartbeat": heartbeat_rows[0]}
    if successful == TASK_DECISION_PROPERTIES:
        task_mapping = property_mapping[DreameMowerProperty.TASK_STATUS]
        task_row = next(
            row
            for row in results
            if isinstance(row, dict)
            and (
                (row.get("siid"), row.get("piid"))
                == (task_mapping["siid"], task_mapping["piid"])
                or (
                    "siid" not in row
                    and "piid" not in row
                    and str(row.get("did"))
                    == str(DreameMowerProperty.TASK_STATUS.value)
                )
            )
        )
        return {"legacy_task_status": task_row["value"]}
    raise DeviceUpdateFailedException(
        "Fresh mower task properties were incomplete or rejected."
    )
