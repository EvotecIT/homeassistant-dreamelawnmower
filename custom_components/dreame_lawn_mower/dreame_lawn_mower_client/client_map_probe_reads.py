"""Owned map-source diagnostics composed from native read-only consumers."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_property_scan import async_scan_properties
from .client_public_reads import async_read_key_definition
from .client_refresh import _run_state_worker
from .client_state_reads import async_read_device_state
from .exceptions import DreameLawnMowerConnectionError
from .map_probe import (
    MAP_HISTORY_PROPERTY_KEYS,
    MAP_PROBE_PROPERTY_KEYS,
    build_map_probe_payload,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_probe_maps(
    client: DreameLawnMowerClient,
    *,
    timeout: float,
    interval: float,
    language: str,
) -> dict[str, Any]:
    cancelled = Event()

    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        selected_map_view = await client.async_refresh_map_view(
            timeout=timeout, interval=interval
        )
        cloud_device_info = await client.async_get_cloud_device_info(language=language)
        cloud_device_list_page = await client.async_get_cloud_device_list_page(
            current=1,
            size=20,
            language=language,
            master=None,
            shared_status=None,
        )
        try:
            cloud_key_definition = await async_read_key_definition(
                client,
                language=language,
                device_info=cloud_device_info,
                device_list_page=cloud_device_list_page,
            )
        except DreameLawnMowerConnectionError as err:
            cloud_key_definition = {"error": str(err)}
        cloud_properties = await async_scan_properties(
            client,
            keys=MAP_PROBE_PROPERTY_KEYS,
            siids=None,
            piid_start=1,
            piid_end=1,
            chunk_size=50,
            language=language,
            only_values=False,
            include_key_definition=False,
            key_definition=(
                cloud_key_definition
                if isinstance(cloud_key_definition, Mapping)
                else None
            ),
        )
        cloud_property_history: dict[str, Any] = {}
        for key in MAP_HISTORY_PROPERTY_KEYS:
            try:
                cloud_property_history[key] = await cloud.async_get_property_history(
                    client._descriptor.did,
                    key,
                    limit=3,
                    time_start=0,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_property_history[key] = {"error": str(err)}
        try:
            cloud_user_features = await client.async_get_cloud_user_features(
                language=language
            )
        except DreameLawnMowerConnectionError as err:
            cloud_user_features = {"error": str(err)}
        try:
            cloud_device_otc_info = await client.async_get_cloud_device_otc_info(
                language=language
            )
        except DreameLawnMowerConnectionError as err:
            cloud_device_otc_info = {"error": str(err)}
        try:
            app_maps = await client.async_get_app_maps(
                chunk_size=400,
                include_payload=False,
                include_objects=True,
                include_object_urls=False,
            )
        except DreameLawnMowerConnectionError as err:
            app_maps = {"error": str(err)}
        legacy_map_view = await _run_state_worker(
            lambda: client._sync_refresh_legacy_map_view(timeout, interval),
            cancelled,
        )
        vector_map_view = await client.async_refresh_vector_map_view()

        return await async_read_device_state(
            client,
            lambda _device: build_map_probe_payload(
                descriptor=client._descriptor,
                map_view=client._map_view_with_cloud_summary(
                    selected_map_view, cloud_properties
                ),
                legacy_map_view=client._map_view_with_cloud_summary(
                    legacy_map_view, cloud_properties
                ),
                vector_map_view=client._map_view_with_cloud_summary(
                    vector_map_view, cloud_properties
                ),
                cloud_properties=cloud_properties,
                cloud_device_info=cloud_device_info,
                cloud_device_list_page=cloud_device_list_page,
                cloud_property_history=cloud_property_history,
                cloud_user_features=cloud_user_features,
                cloud_device_otc_info=cloud_device_otc_info,
                cloud_key_definition=cloud_key_definition,
                app_maps=app_maps,
            ),
            refresh=False,
        )

    return await client._async_cloud_read(read)
