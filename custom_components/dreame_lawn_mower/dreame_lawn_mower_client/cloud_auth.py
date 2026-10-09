"""Validated authentication state shared by native HTTP and legacy MQTT setup."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .exceptions import DreameLawnMowerAuthError


@dataclass(frozen=True, repr=False)
class CloudAuthentication:
    """A complete login result; its representation never includes credentials."""

    token: str
    refresh_token: str | None
    expires_at: float
    tenant: str | None
    user_id: str | None
    region: str | None


def parse_cloud_authentication(
    payload: object, strings: Sequence[str], *, now: float,
    tenant: str | None = None, region: str | None = None,
) -> CloudAuthentication:
    """Validate a whole login response before either transport mutates state."""
    if not isinstance(payload, Mapping):
        raise DreameLawnMowerAuthError("Cloud authentication response is invalid")
    data: Mapping[str, object] = payload
    token = data.get(strings[18])
    expires = data.get(strings[20])
    refresh = data.get(strings[19])
    tenant_value = data.get(strings[22], tenant)
    region_value = data.get(strings[21], region)
    user_id = data.get("uid")
    if (
        not isinstance(token, str) or not token
        or not isinstance(expires, int | float) or isinstance(expires, bool)
        or not math.isfinite(expires) or expires <= 0
        or (refresh is not None and not isinstance(refresh, str))
        or (tenant_value is not None and not isinstance(tenant_value, str))
        or (region_value is not None and not isinstance(region_value, str))
        or (user_id is not None and (
            not isinstance(user_id, str | int) or isinstance(user_id, bool)
        ))
    ):
        raise DreameLawnMowerAuthError("Cloud authentication response is invalid")
    return CloudAuthentication(
        token=token,
        refresh_token=refresh,
        expires_at=now + expires - min(120, expires / 2),
        tenant=tenant_value,
        user_id=str(user_id) if user_id is not None else None,
        region=region_value,
    )
