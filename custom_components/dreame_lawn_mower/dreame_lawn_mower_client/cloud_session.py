"""Native async cloud login and account requests using a borrowed session."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .cloud_wire import cloud_headers, cloud_login_data, cloud_strings
from .exceptions import DreameLawnMowerAuthError, DreameLawnMowerConnectionError

MAX_CLOUD_RESPONSE_BYTES = 1024 * 1024


class DreameCloudSession:
    """Serialize cloud operations without owning the caller's HTTP session.

    Cancellation releases the active response and operation lock. Authentication
    state changes only after a complete, validated response. No response bodies,
    credentials, signed URLs, or authentication headers are logged.
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        username: str,
        password: str,
        country: str,
        account_type: str,
    ) -> None:
        country = country.lower()
        if re.fullmatch(r"[a-z]{2}", country) is None:
            raise DreameLawnMowerAuthError("Invalid cloud country")
        self._session = session
        self._strings = cloud_strings(account_type)
        self._username = username
        self._password = password
        self._country = country
        self._tenant: str | None = None
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def _base_url(self) -> str:
        return f"https://{self._country}{self._strings[0]}:{self._strings[1]}"

    async def async_login(
        self,
        *,
        timeout: float = 10,
        deadline: float | None = None,
    ) -> None:
        """Authenticate within a deadline that includes waiting for another call."""
        end = self._deadline(timeout, deadline)
        try:
            async with asyncio.timeout(self._remaining(end)):
                async with self._lock:
                    await self._login(end)
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError("Cloud login timed out") from err
        except ClientError as err:
            raise DreameLawnMowerConnectionError(
                "Cloud login connection failed"
            ) from err

    async def async_get_devices(
        self,
        *,
        timeout: float = 20,
        deadline: float | None = None,
    ) -> Any:
        """Read the account inventory, including authentication in the deadline."""
        end = self._deadline(timeout, deadline)
        try:
            async with asyncio.timeout(self._remaining(end)):
                async with self._lock:
                    if self._token is None or time.time() >= self._expires_at:
                        await self._login(end)
                    path = "/".join(self._strings[index] for index in (23, 24, 27, 28))
                    for attempt in range(2):
                        headers = cloud_headers(
                            self._strings,
                            self._country,
                            self._tenant,
                        )
                        headers[self._strings[51]] = self._strings[52]
                        assert self._token is not None
                        headers[self._strings[46]] = self._token
                        status, payload = await self._post_inventory(
                            f"{self._base_url}/{path}",
                            headers,
                            end,
                        )
                        if status == 401 and attempt == 0:
                            await self._login(end)
                            continue
                        if status != 200:
                            raise DreameLawnMowerConnectionError(
                                f"Cloud inventory request failed: HTTP {status}"
                            )
                        if payload.get("code") != 0:
                            raise DreameLawnMowerConnectionError(
                                "Cloud inventory request was rejected"
                            )
                        return payload.get("data")
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError("Cloud inventory timed out") from err
        except ClientError as err:
            raise DreameLawnMowerConnectionError(
                "Cloud inventory connection failed"
            ) from err
        raise DreameLawnMowerConnectionError("Cloud inventory request failed")

    async def _login(self, deadline: float) -> None:
        for attempt in range(2):
            status, payload = await self._post(
                self._base_url + self._strings[17],
                cloud_headers(self._strings, self._country, self._tenant),
                cloud_login_data(
                    self._strings,
                    self._username,
                    self._password,
                    self._refresh_token,
                ),
                deadline,
            )
            if status != 200:
                description = payload.get("error_description")
                if (
                    attempt == 0
                    and self._refresh_token
                    and isinstance(description, str)
                    and "refresh token" in description
                ):
                    self._refresh_token = None
                    continue
                raise DreameLawnMowerAuthError(
                    f"Cloud authentication failed: HTTP {status}"
                )
            token = payload.get(self._strings[18])
            expires = payload.get(self._strings[20])
            refresh = payload.get(self._strings[19])
            tenant = payload.get(self._strings[22], self._tenant)
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires, int | float)
                or isinstance(expires, bool)
                or not math.isfinite(expires)
                or expires <= 0
                or (refresh is not None and not isinstance(refresh, str))
                or (tenant is not None and not isinstance(tenant, str))
            ):
                raise DreameLawnMowerAuthError(
                    "Cloud authentication response is invalid"
                )
            self._token = token
            self._refresh_token = refresh
            self._tenant = tenant
            self._expires_at = time.time() + expires - min(120, expires / 2)
            return
        raise DreameLawnMowerAuthError("Cloud authentication failed")

    async def _post_inventory(
        self,
        url: str,
        headers: Mapping[str, str],
        deadline: float,
    ) -> tuple[int, dict[str, Any]]:
        """Retry only the read-only inventory request within its shared deadline."""
        for attempt in range(3):
            try:
                return await self._post(url, headers, None, deadline)
            except (ClientError, TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(
                    min((0.25, 1.0)[attempt], self._remaining(deadline))
                )
        raise AssertionError("Inventory retry loop exhausted")

    async def _post(
        self,
        url: str,
        headers: Mapping[str, str],
        data: str | None,
        deadline: float,
    ) -> tuple[int, dict[str, Any]]:
        async with self._session.post(
            url,
            headers=headers,
            data=data,
            timeout=ClientTimeout(total=self._remaining(deadline)),
            allow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                if len(body) + len(chunk) > MAX_CLOUD_RESPONSE_BYTES:
                    raise DreameLawnMowerConnectionError(
                        "Cloud response exceeds the size limit"
                    )
                body.extend(chunk)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError) as err:
                raise DreameLawnMowerConnectionError(
                    "Cloud response is not valid JSON"
                ) from err
            if not isinstance(payload, dict):
                raise DreameLawnMowerConnectionError("Cloud response is not an object")
            return response.status, payload

    @staticmethod
    def _deadline(timeout: float, deadline: float | None) -> float:
        if not math.isfinite(timeout) or (
            deadline is not None and not math.isfinite(deadline)
        ):
            raise ValueError("Cloud timeout and deadline must be finite")
        end = time.monotonic() + timeout
        return min(end, deadline) if deadline is not None else end

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Cloud operation timed out")
        return remaining
