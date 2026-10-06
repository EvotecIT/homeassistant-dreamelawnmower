"""Owned native HTTP transport for the shared camera credential workflow."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from .client_state_reads import async_read_device_state
from .cloud_video import VideoReadKind
from .cloud_wire import cloud_strings
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

_VIDEO_METHODS: dict[str, VideoReadKind] = {
    "get_tx_video_access_token": "access_token",
    "get_tx_video_device_identity": "identity",
    "get_tx_video_p2p_info": "p2p",
    "get_tx_video_user_eligibility": "eligibility",
}


async def async_camera_stream_inputs(
    client: DreameLawnMowerClient,
) -> dict[str, Any]:
    """Retain stage diagnostics while owning native reads through cancellation."""
    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        plan = client._camera_stream_inputs_plan()
        protocol = None
        try:
            method, arguments = next(plan)
            while True:
                try:
                    if method == "setup":
                        protocol = await async_read_device_state(
                            client, lambda device: device._protocol.cloud,
                            refresh=False,
                        )
                        if protocol is None:
                            raise DreameLawnMowerConnectionError(
                                "Cloud connection is unavailable."
                            )
                        await cloud.async_ensure_authenticated()
                        response: Any = {
                            "logged_in": True, "methods": set(_VIDEO_METHODS),
                        }
                    else:
                        kind = _VIDEO_METHODS[method]
                        timeout = 5 if kind == "eligibility" else 20
                        deadline = time.monotonic() + timeout
                        uid = model = None
                        if kind == "identity":
                            assert protocol is not None
                            async with protocol.async_rpc_operation(deadline=deadline):
                                uid, model = protocol._uid, protocol._model
                            if not uid:
                                info = await cloud.async_get_device_info(
                                    client._descriptor.did, deadline=deadline,
                                )
                                if info:
                                    if str(info.get("did")) != client._descriptor.did:
                                        raise DreameLawnMowerConnectionError(
                                            "Cloud device identity is invalid"
                                        )
                                    strings = cloud_strings(client._account_type)
                                    uid = info.get(strings[8])
                                    model = info.get(strings[35])
                        response = await cloud.async_get_video_data(
                            kind, client._descriptor.did,
                            uid=str(uid) if uid else None, model=model,
                            deadline=deadline, **arguments,
                        )
                except Exception as error:
                    method, arguments = plan.throw(error)
                else:
                    method, arguments = plan.send(response)
        except StopIteration as completed:
            return cast(dict[str, Any], completed.value)
        finally:
            plan.close()

    return await client._async_cloud_read(read)
