from types import SimpleNamespace

from custom_components.dreame_lawn_mower import _cached_video_only_available
from custom_components.dreame_lawn_mower.const import (
    CONF_VIDEO_TRANSPORT,
    VIDEO_TRANSPORT_AUTO,
    VIDEO_TRANSPORT_CLOUD,
)


def test_auto_setup_allows_complete_cached_xp2p_provisioning() -> None:
    assert _cached_video_only_available(
        SimpleNamespace(options={CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_AUTO}),
        lan_cache=SimpleNamespace(inputs=None, endpoint=None),
        provisioning_cache=SimpleNamespace(inputs=object(), device_config=object()),
    )


def test_auto_setup_allows_proven_cached_lan_endpoint() -> None:
    assert _cached_video_only_available(
        SimpleNamespace(options={CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_AUTO}),
        lan_cache=SimpleNamespace(inputs=object(), endpoint=object()),
        provisioning_cache=SimpleNamespace(inputs=None, device_config=None),
    )


def test_cloud_setup_never_uses_private_video_cache_as_device_state() -> None:
    assert not _cached_video_only_available(
        SimpleNamespace(options={CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_CLOUD}),
        lan_cache=SimpleNamespace(inputs=object(), endpoint=object()),
        provisioning_cache=SimpleNamespace(inputs=object(), device_config=object()),
    )
