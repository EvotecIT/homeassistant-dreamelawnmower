"""Fresh property evidence required before state-changing command decisions."""

from collections.abc import Mapping, Sequence

from .app_protocol import MOWER_RAW_STATUS_PROPERTY_KEY, decode_mower_status_blob
from .device_types import DreameMowerProperty
from .exceptions import DeviceUpdateFailedException

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
        if mapping is not None and "aiid" not in mapping and (
            not ready or prop.value in known_properties
            or (require_fresh_state and prop in TASK_DECISION_PROPERTIES)
        ):
            requests.append({"did": str(prop.value), **mapping})
    if require_fresh_state:
        # Current firmware carries task evidence in the native heartbeat. A
        # fresh read must request it even when no prior MQTT value was cached.
        requests.append({"did": "100001", "siid": 1, "piid": 1})
    return requests


def _successful_row(rows):
    return (
        len(rows) == 1 and rows[0].get("code") == 0 and rows[0].get("value") is not None
    )


def require_fresh_task_properties(results, property_mapping):
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
