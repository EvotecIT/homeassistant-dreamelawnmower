"""Device-scoped video requests require a selected device."""

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_video import (
    video_read_params,
)


@pytest.mark.parametrize("kind", ["identity", "eligibility", "p2p"])
@pytest.mark.parametrize("did", [None, ""])
def test_device_video_read_requires_identity(kind, did):
    with pytest.raises(ValueError, match="Device ID is required"):
        video_read_params(kind, did)


def test_account_token_read_does_not_require_device():
    assert video_read_params("access_token", None) == {"os": 1}
