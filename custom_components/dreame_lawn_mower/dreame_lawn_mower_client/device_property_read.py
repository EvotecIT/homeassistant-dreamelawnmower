"""Fresh property evidence required before state-changing command decisions."""

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
            return heartbeat_rows[0]
    if successful == TASK_DECISION_PROPERTIES:
        return None
    raise DeviceUpdateFailedException(
        "Fresh mower task properties were incomplete or rejected."
    )
