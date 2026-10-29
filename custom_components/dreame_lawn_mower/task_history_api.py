"""Authenticated task history and private photo delivery for dashboard readers."""

from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.http import HomeAssistantView

from .const import DOMAIN
from .dreame_lawn_mower_client.task_history import finite_number, task_mode, timestamp
from .runtime_data import get_coordinator
from .task_history_store import TaskHistoryOwner

TASK_HISTORY_PATH = f"/api/{DOMAIN}/task-history"
_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Authorization",
    "X-Content-Type-Options": "nosniff",
}


def task_history_api_path(entry_id: str) -> str:
    """Advertise an authenticated HA path without cloud credentials."""
    return f"{TASK_HISTORY_PATH}/{entry_id}"


class TaskHistoryAPI:
    """Resolve permissions and the current entry owner for every request."""

    def __init__(self, hass: Any) -> None:
        self.hass = hass

    def authorize(self, request: web.Request, entry_id: str) -> TaskHistoryOwner:
        coordinator = get_coordinator(self.hass, entry_id)
        if coordinator is None:
            raise web.HTTPNotFound(headers=_HEADERS)
        user = request.get("hass_user")
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "lawn_mower",
            DOMAIN,
            f"{coordinator.client.descriptor.unique_id}_mower",
        )
        if (
            user is None
            or entity_id is None
            or not user.permissions.check_entity(entity_id, POLICY_READ)
        ):
            raise web.HTTPForbidden(headers=_HEADERS)
        owner = getattr(coordinator, "task_history", None)
        if not isinstance(owner, TaskHistoryOwner) or owner.closed:
            raise web.HTTPNotFound(headers=_HEADERS)
        return owner

    def current(self, entry_id: str, owner: TaskHistoryOwner) -> None:
        coordinator = get_coordinator(self.hass, entry_id)
        if (
            coordinator is not owner.coordinator
            or owner.closed
            or getattr(coordinator, "task_history", None) is not owner
        ):
            raise web.HTTPNotFound(headers=_HEADERS)

    async def read(self, entry_id: str, owner: TaskHistoryOwner, key: str) -> Any:
        try:
            value = await owner.async_read(key)
        except Exception as err:
            self.current(entry_id, owner)
            raise web.HTTPBadGateway(
                text="Task history could not be loaded. Try again later.",
                headers={**_HEADERS, "Retry-After": "30"},
            ) from err
        self.current(entry_id, owner)
        return value


class TaskHistoryListView(HomeAssistantView):
    url = f"{TASK_HISTORY_PATH}/{{entry_id}}"
    name = "api:dreame_lawn_mower:task_history"
    requires_auth = True

    def __init__(self, api: TaskHistoryAPI) -> None:
        self.api = api

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        owner = self.api.authorize(request, entry_id)
        await self.api.read(entry_id, owner, "list")
        tasks = []
        for key, session in owner.history.sessions.items():
            detail = owner.history.details.get(key)
            tasks.append(
                {
                    "id": key,
                    "mode": task_mode(session.task),
                    "started_at": timestamp(session.started_at),
                    "reported_at": (
                        timestamp(session.created_at_ms // 1000)
                        if type(session.created_at_ms) is int
                        else None
                    ),
                    "duration_minutes": finite_number(session.duration_minutes),
                    "area_sqm": finite_number(session.area_sqm),
                    "result": session.result,
                    "detail_path": f"{task_history_api_path(entry_id)}/{key}",
                    "detail_available": detail is not None,
                }
            )
        return web.json_response(
            {
                "schema_version": 1,
                "tasks": tasks,
                "limit": 30,
                "stale": owner.history.stale,
            },
            headers=_HEADERS,
        )


class TaskHistoryDetailView(HomeAssistantView):
    url = f"{TASK_HISTORY_PATH}/{{entry_id}}/{{task_id}}"
    name = "api:dreame_lawn_mower:task_history_detail"
    requires_auth = True

    def __init__(self, api: TaskHistoryAPI) -> None:
        self.api = api

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        task_id: str,
    ) -> web.Response:
        owner = self.api.authorize(request, entry_id)
        detail = await self.api.read(entry_id, owner, task_id)
        if detail is None:
            raise web.HTTPNotFound(
                text="This task summary is unavailable.", headers=_HEADERS
            )
        payload = detail.as_dict()
        payload["stale"] = owner.history.stale
        payload["battery"] = owner.battery_for_task(detail)
        payload["photo_paths"] = [
            f"{task_history_api_path(entry_id)}/{task_id}/photo/{index}"
            for index in range(detail.photo_count)
        ]
        return web.json_response({"schema_version": 1, **payload}, headers=_HEADERS)


class TaskHistoryPhotoView(HomeAssistantView):
    url = f"{TASK_HISTORY_PATH}/{{entry_id}}/{{task_id}}/photo/{{index}}"
    name = "api:dreame_lawn_mower:task_history_photo"
    requires_auth = True

    def __init__(self, api: TaskHistoryAPI) -> None:
        self.api = api

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        task_id: str,
        index: str,
    ) -> web.Response:
        owner = self.api.authorize(request, entry_id)
        if not index.isascii() or not index.isdecimal() or len(index) > 3:
            raise web.HTTPNotFound(headers=_HEADERS)
        number = int(index)
        await self.api.read(entry_id, owner, task_id)
        photos = owner.history.photos.get(task_id, ())
        if number >= len(photos):
            raise web.HTTPNotFound(headers=_HEADERS)
        # Keep the transfer under the same entry-owned worker lifetime.
        try:
            content = await owner.async_photo(task_id, number)
        except (web.HTTPNotFound, web.HTTPGone):
            raise
        except Exception as err:
            self.api.current(entry_id, owner)
            raise web.HTTPBadGateway(
                text="Task photo could not be loaded. Try again later.",
                headers={**_HEADERS, "Retry-After": "30"},
            ) from err
        self.api.current(entry_id, owner)
        if content is None:
            raise web.HTTPNotFound(headers=_HEADERS)
        mime = "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        if mime == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
            raise web.HTTPBadGateway(headers=_HEADERS)
        return web.Response(body=content, content_type=mime, headers=_HEADERS)


def async_setup_task_history_api(hass: Any) -> None:
    """Register the versioned read-only API once per HA instance."""
    data = hass.data.setdefault(DOMAIN, {})
    if "task_history_api" not in data:
        api = TaskHistoryAPI(hass)
        for view in (TaskHistoryListView, TaskHistoryDetailView, TaskHistoryPhotoView):
            hass.http.register_view(view(api))
        data["task_history_api"] = api
