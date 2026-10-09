"""Legacy protocol facade and MiIO transport compatibility."""

from __future__ import annotations

import logging
import random
import hashlib
import json
import base64
import hmac
import requests
import zlib
import queue
from collections.abc import Callable
from threading import Thread, Timer
from time import sleep
import time
import locale
from datetime import datetime
from paho.mqtt import client as mqtt_client
from typing import Any, Dict, Mapping, Optional, Tuple
from Crypto.Cipher import ARC4
from miio.miioprotocol import MiIOProtocol

from .exceptions import DeviceException
from .protocol_queue import RequestQueue, ResponseCallback, run_callback_queue
from .const import DREAME_STRINGS, MOVA_STRINGS
from .deadline import DeadlineExceededError, run_with_deadline
from .mqtt_tls import create_cloud_mqtt_ssl_context

_LOGGER = logging.getLogger(__name__)
_TX_VIDEO_API_PATH = "/dreame-third-video/tx/"
_REDACTED_TX_VIDEO_PAYLOAD = "<redacted TX video payload>"
_INTERIM_FILE_API_PATH = "/dreame-user-iot/iotfile/getDownloadUrl"
_REDACTED_INTERIM_FILE_PAYLOAD = "<redacted interim file payload>"
_REDACTED_APP_ACTION_RESPONSE = "<redacted app action response>"
_DEADLINE_RESPONSE_CHUNK_BYTES = 8 * 1024
_MAX_DEADLINE_RESPONSE_BYTES = 1024 * 1024


from .protocol_cloud import (
    DreameMowerDreameHomeCloudProtocol,
    _app_action_response_log_value,
    _cloud_request_log_value,
    _post_cloud_response,
    _read_cloud_response_text_with_deadline,
    _set_cloud_response_timeout,
)

class DreameMowerDeviceProtocol(MiIOProtocol):
    def __init__(self, ip: str, token: str) -> None:
        super().__init__(ip, token, 0, 0, True, 2)
        self._queue = RequestQueue()
        self._thread: Thread | None = None
        self.set_credentials(ip, token)

    def _api_task(self) -> None:
        run_callback_queue(self._queue, self.send, _LOGGER)

    def send_async(
        self,
        callback: ResponseCallback | None,
        command: str,
        parameters: Any = None,
        retry_count: int = 2,
    ) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._api_task, daemon=True)
            self._thread.start()

        self._queue.put((callback, command, parameters, retry_count))

    def set_credentials(self, ip: str, token: str | None) -> None:
        token_bytes = bytes.fromhex(token or 32 * "0")
        if self.ip != ip or self.token != token_bytes:
            self.ip = ip
            self.port = 54321
            self.token = token_bytes
            self._discovered = False

    @property
    def connected(self) -> bool:
        return self._discovered

    def disconnect(self) -> None:
        self._discovered = False
        if self._thread:
            self._queue.stop()


class DreameMowerProtocol:
    def __init__(
        self,
        ip: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        country: str | None = None,
        prefer_cloud: bool = True,
        account_type: str = "dreame",
        device_id: str | None = None,
    ) -> None:
        if account_type != "dreame" and account_type != "mova":
            raise DeviceException(
                "DreameMowerProtocol: unsupported account_type: %s", account_type) from None

        if not prefer_cloud:
            raise DeviceException(
                "DreameMowerProtocol: work only with cloud") from None

        self.prefer_cloud = prefer_cloud
        self._connected = False
        self._mac: str | None = None
        self._account_type = account_type

        self.prefer_cloud = True
        self.device: DreameMowerDeviceProtocol | None = None

        self.cloud = DreameMowerDreameHomeCloudProtocol(
            username, password, country, device_id, account_type)
        self.device_cloud = self.cloud

    def set_credentials(
        self, ip: str, token: str, mac: str | None = None, account_type: str = "mi"
    ) -> None:
        self._mac = mac
        self._account_type = account_type
        if ip and token and account_type == "mi":
            if self.device:
                self.device.set_credentials(ip, token)
            else:
                self.device = DreameMowerDeviceProtocol(ip, token)
        else:
            self.device = None

    def connect(
        self,
        message_callback: ResponseCallback | None = None,
        connected_callback: Callable[[], None] | None = None,
        retry_count: int = 1,
    ) -> Any:
        info = self.cloud.connect(message_callback, connected_callback)
        if info:
            self._connected = True
        return info

    def disconnect(self) -> None:
        disconnected_protocols: set[int] = set()
        for cloud in (self.cloud, self.device_cloud):
            if cloud is None or id(cloud) in disconnected_protocols:
                continue
            disconnected_protocols.add(id(cloud))
            cloud.disconnect()
        self._connected = False

    def send_async(
        self,
        callback: ResponseCallback,
        method: str,
        parameters: Any = None,
        retry_count: int = 0,
    ) -> None:
        if not self.device_cloud:
            raise DeviceException("Cloud connection missing") from None

        if not self.device_cloud.logged_in:
            # Use different session for device cloud
            self.device_cloud.login()
            if self.device_cloud.logged_in and not self.device_cloud.device_id:
                if self.cloud.device_id:
                    self.device_cloud._did = self.cloud.device_id
                elif self._mac:
                    self.device_cloud.get_info(self._mac)

        if not self.device_cloud.logged_in:
            raise DeviceException(
                "Unable to login to device over cloud") from None

        def cloud_callback(response: Any) -> None:
            self._connected = response is not None
            callback(response)

        self.device_cloud.send_async(
            cloud_callback, method, parameters=parameters, retry_count=retry_count)

    def send(
        self,
        method: str,
        parameters: Any = None,
        retry_count: int = 0,
        *,
        deadline: float | None = None,
    ) -> Any:
        if not self.device_cloud:
            raise DeviceException("Cloud connection missing") from None

        if not self.device_cloud.logged_in:
            _LOGGER.info("send: Not logged in over cloud. Try to log in.")
            # Use different session for device cloud
            if deadline is None:
                self.device_cloud.login()
            else:
                self.device_cloud.login(deadline=deadline)
            if self.device_cloud.logged_in and not self.device_cloud.device_id:
                if self.cloud.device_id:
                    _LOGGER.info("send: cloud device id")
                    self.device_cloud._did = self.cloud.device_id
                elif self._mac:
                    _LOGGER.info("send: using _mac")
                    if deadline is None:
                        self.device_cloud.get_info(self._mac)
                    else:
                        self.device_cloud.get_info(self._mac, deadline=deadline)

        if not self.device_cloud.logged_in:
            raise DeviceException(
                "Unable to login to device over cloud") from None

        _LOGGER.debug("DreameMowerProtocol.send %s %s", method, parameters)
        if deadline is None:
            response = self.device_cloud.send(
                method, parameters=parameters, retry_count=retry_count,
            )
        else:
            response = self.device_cloud.send(
                method, parameters=parameters, retry_count=retry_count, deadline=deadline,
            )
        _LOGGER.debug("DreameMowerProtocol.send response %s", response)
        return response

    def get_properties(
        self,
        parameters: Any = None,
        retry_count: int = 1,
        *,
        deadline: float | None = None,
    ) -> Any:
        if deadline is None:
            return self.send(
                "get_properties",
                parameters=parameters,
                retry_count=retry_count,
            )
        return self.send(
            "get_properties",
            parameters=parameters,
            retry_count=retry_count,
            deadline=deadline,
        )

    def set_property(
        self, siid: int, piid: int, value: Any = None, retry_count: int = 0
    ) -> Any:
        return self.set_properties(
            [
                {
                    "did": f"{siid}.{piid}" if not self.dreame_cloud else str(self.cloud.device_id),
                    "siid": siid,
                    "piid": piid,
                    "value": value,
                }
            ],
            retry_count=retry_count,
        )

    def set_properties(self, parameters: Any = None, retry_count: int = 0) -> Any:
        return self.send("set_properties", parameters=parameters, retry_count=retry_count)

    def action_async(
        self,
        callback: ResponseCallback,
        siid: int,
        aiid: int,
        parameters: Any = [],
        retry_count: int = 0,
    ) -> None:
        if parameters is None:
            parameters = []

        _LOGGER.debug("Send Action Async: %s.%s %s", siid, aiid, parameters)
        self.send_async(
            callback,
            "action",
            parameters={
                "did": f"{siid}.{aiid}" if not self.dreame_cloud else str(self.cloud.device_id),
                "siid": siid,
                "aiid": aiid,
                "in": parameters,
            },
            retry_count=retry_count,
        )

    def action(
        self, siid: int, aiid: int, parameters: Any = [], retry_count: int = 0
    ) -> Any:
        if parameters is None:
            parameters = []

        _LOGGER.debug("Send Action: %s.%s %s", siid, aiid, parameters)
        return self.send(
            "action",
            parameters={
                "did": f"{siid}.{aiid}" if not self.dreame_cloud else str(self.cloud.device_id),
                "siid": siid,
                "aiid": aiid,
                "in": parameters,
            },
            retry_count=retry_count,
        )

    @property
    def connected(self) -> bool:
        if not self.device_cloud:
            raise DeviceException("Cloud connection missing") from None
        return bool(self.device_cloud.logged_in and self.device_cloud.connected and self._connected)

    @property
    def dreame_cloud(self) -> bool:
        if not self.cloud:
            raise DeviceException("Cloud connection missing") from None
        return self.cloud.dreame_cloud
