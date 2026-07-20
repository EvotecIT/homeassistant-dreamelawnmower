"""Contract tests for sanitized recent diagnostic events."""

from custom_components.dreame_lawn_mower.diagnostic_events import (
    DreameLawnMowerDiagnosticEventStore,
)


def test_diagnostic_event_store_redacts_and_coalesces_duplicates() -> None:
    store = DreameLawnMowerDiagnosticEventStore(limit=3)

    store.record(
        code="video_cloud_start_failed",
        source="video_camera",
        message="request failed token=secret-value",
        context={"appSecret": "secret", "transport": "cloud"},
    )
    store.record(
        code="video_cloud_start_failed",
        source="video_camera",
        message="request failed token=secret-value",
        context={"appSecret": "secret", "transport": "cloud"},
    )

    events = store.as_list()
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert events[0]["message"] == "request failed token=**REDACTED**"
    assert events[0]["context"] == {
        "appSecret": "**REDACTED**",
        "transport": "cloud",
    }
    assert "_fingerprint" not in events[0]


def test_diagnostic_event_store_keeps_only_the_recent_bounded_history() -> None:
    store = DreameLawnMowerDiagnosticEventStore(limit=2)

    for index in range(3):
        store.record(
            code=f"event_{index}",
            source="test",
            message=f"failure {index}",
        )

    assert [event["code"] for event in store.as_list()] == ["event_1", "event_2"]
