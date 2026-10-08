"""Shared request and response contracts for read-only TX video provisioning."""

from __future__ import annotations

from typing import Any, Literal

VideoReadKind = Literal["access_token", "identity", "p2p", "eligibility"]
VIDEO_READ_PATHS: dict[VideoReadKind, str] = {
    "access_token": "/dreame-third-video/tx/user/accesstoken",
    "identity": "/dreame-third-video/tx/mgr/dev/getIdentity",
    "p2p": "/dreame-third-video/tx/dev/getP2PInfo",
    "eligibility": "/dreame-third-video/tx/dev/isDevUser",
}


def video_read_params(
    kind: VideoReadKind, did: str | None, access_token: str | None = None,
    os: int = 1, uid: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    """Retain both vendor token spellings and identity-only account fields."""
    if kind == "access_token":
        return {"os": os}
    params: dict[str, Any] = {"did": did, "os": os}
    if access_token:
        params["accesstoken"] = access_token
        params["accessToken"] = access_token
    if kind == "identity":
        if uid:
            params["uid"] = str(uid)
        if model:
            params["model"] = model
    return params


def video_read_result(response: Any) -> Any:
    """Unwrap successful data, retaining vendor failures for diagnostics."""
    if response and "data" in response and response.get("code", 0) == 0:
        return response["data"]
    return response
