"""Startup readiness must remain distinct from verified media."""

from custom_components.dreame_lawn_mower.video_startup_timing import VideoStartupTiming


def test_video_timing_accumulates_phases_and_verifies_media_separately():
    clock = [1.0]
    timing = VideoStartupTiming(lambda: clock[0])
    clock[0] = 1.1
    timing.enter("provisioning")
    clock[0] = 1.4
    timing.enter("cloud_transport")
    clock[0] = 2.0
    timing.finish("source_ready")
    ready = timing.as_dict()
    assert ready["total_ms"] == 1000
    assert ready["phases_ms"] == {
        "safety": 100, "provisioning": 300, "cloud_transport": 600
    }
    assert ready["verified_media_ms"] is None
    clock[0] = 2.5
    timing.verified()
    clock[0] = 3.0
    timing.verified()
    assert timing.as_dict()["verified_media_ms"] == 1500
    assert timing.as_dict()["total_ms"] == 1000
