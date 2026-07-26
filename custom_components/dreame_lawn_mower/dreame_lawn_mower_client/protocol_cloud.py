"""Dreame Home cloud transport and authentication protocol."""

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
from threading import RLock, Thread, Timer
from time import sleep
import time
import locale
from datetime import datetime
from paho.mqtt import client as mqtt_client
from typing import Any, Dict, Mapping, Optional, Tuple
from Crypto.Cipher import ARC4
from miio.miioprotocol import MiIOProtocol

from .exceptions import DeviceException, DreameLawnMowerCloudAPIError
from .const import DREAME_STRINGS, MOVA_STRINGS
from .deadline import DeadlineExceededError, run_with_deadline
from .mqtt_tls import create_cloud_mqtt_ssl_context

_LOGGER = logging.getLogger(__name__)
_TX_VIDEO_API_PATH = "/dreame-third-video/tx/"
_REDACTED_TX_VIDEO_PAYLOAD = "<redacted TX video payload>"
_INTERIM_FILE_API_PATH = "/dreame-user-iot/iotfile/getDownloadUrl"
_REDACTED_INTERIM_FILE_PAYLOAD = "<redacted interim file payload>"
_CLOUD_PROPERTIES_API_PATH = "/dreame-user-iot/iotstatus/props"
_REDACTED_CLOUD_PROPERTIES_PAYLOAD = "<redacted cloud properties payload>"
_REDACTED_APP_ACTION_RESPONSE = "<redacted app action response>"
_DEADLINE_RESPONSE_CHUNK_BYTES = 8 * 1024
_MAX_DEADLINE_RESPONSE_BYTES = 1024 * 1024



def _cloud_request_log_value(url: str, value: Any) -> Any:
    """Hide sensitive cloud request and response material from logs."""
    if _TX_VIDEO_API_PATH in url and value is not None:
        return _REDACTED_TX_VIDEO_PAYLOAD
    if _INTERIM_FILE_API_PATH in url and value is not None:
        return _REDACTED_INTERIM_FILE_PAYLOAD
    if _CLOUD_PROPERTIES_API_PATH in url and value is not None:
        return _REDACTED_CLOUD_PROPERTIES_PAYLOAD
    return value
def _app_action_response_log_value(value: Any, *, redact: bool) -> Any:
    """Hide private point-cloud object names from app-action response logs."""
    if redact and value is not None:
        return _REDACTED_APP_ACTION_RESPONSE
    return value
def _set_cloud_response_timeout(response: Any, timeout: float) -> None:
    """Apply the remaining overall deadline to a streamed response socket."""
    raw = getattr(response, "raw", None)
    raw_fp = getattr(raw, "_fp", None)
    response_fp = getattr(raw_fp, "fp", None)
    response_raw = getattr(response_fp, "raw", None)
    candidates = (
        response,
        raw,
        getattr(raw, "_sock", None),
        raw_fp,
        response_fp,
        response_raw,
        getattr(response_raw, "_sock", None),
    )
    for candidate in candidates:
        set_timeout = getattr(candidate, "settimeout", None)
        if callable(set_timeout):
            set_timeout(timeout)
            return
    raise requests.exceptions.Timeout(
        "The cloud response deadline could not be enforced."
    )
def _read_cloud_response_text_with_deadline(
    response: Any,
    *,
    deadline: float,
    max_bytes: int = _MAX_DEADLINE_RESPONSE_BYTES,
) -> str:
    """Read a bounded response body while enforcing an absolute deadline."""
    raw = getattr(response, "raw", None)
    read_chunk = getattr(raw, "read1", None)
    if not callable(read_chunk):
        raise requests.exceptions.Timeout(
            "The cloud response deadline could not be enforced."
        )

    content = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("The cloud response timed out.")
        _set_cloud_response_timeout(response, remaining)
        chunk = read_chunk(
            min(
                _DEADLINE_RESPONSE_CHUNK_BYTES,
                max_bytes + 1 - len(content),
            ),
            decode_content=True,
        )
        if time.monotonic() >= deadline:
            raise requests.exceptions.Timeout("The cloud response timed out.")
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ValueError("The cloud response exceeded the maximum size.")

    encoding = getattr(response, "encoding", None) or "utf-8"
    return content.decode(encoding)
def _post_cloud_response(
    session: Any,
    url: str,
    request_options: dict[str, Any],
    *,
    deadline: float | None,
    on_dispatch: Callable[[], None] | None = None,
) -> Any:
    """Post while bounding the response-header phase by an absolute deadline."""
    def post() -> Any:
        if on_dispatch is not None:
            on_dispatch()
        return session.post(url, **request_options)

    if deadline is None:
        return post()
    try:
        return run_with_deadline(
            post,
            deadline=deadline,
        )
    except DeadlineExceededError as err:
        raise requests.exceptions.Timeout(
            "The cloud response timed out."
        ) from err


class DreameMowerDreameHomeCloudProtocol:
    def __init__(self, username: str, password: str, country: str = "cn", did: str = None, account_type: str = "dreame") -> None:
        self.two_factor_url = None
        self._account_type = account_type
        self._username = username
        self._password = password
        self._country = country
        self._location = country
        self._did = did
        self._request_lock = RLock()
        self._session = requests.session()
        self._queue = queue.Queue()
        self._thread = None
        self._id = random.randint(1, 100)
        self._reconnect_timer = None
        self._host = None
        self._model = None
        self._ti = None
        self._fail_count = 0
        self._connected = False
        self._client_connected = False
        self._client_connecting = False
        self._client = None
        self._message_callback = None
        self._connected_callback = None
        self._logged_in = None
        self._stream_key = None
        self._client_key = None
        self._secondary_key = None
        self._key_expire = None
        self._key = None
        self._uid = None
        self._uuid = None
        self._strings = None

    def _operation_lock(self):
        """Return the shared cloud lock, including legacy constructed fixtures."""
        lock = getattr(self, "_request_lock", None)
        if lock is None:
            lock = RLock()
            self._request_lock = lock
        return lock

    def _api_task(self):
        while True:
            item = self._queue.get()
            if len(item) == 0:
                self._queue.task_done()
                return
            item[0](self._api_call(item[1], item[2], item[3]))
            sleep(0.1)
            self._queue.task_done()

    def _api_call_async(self, callback, url, params=None, retry_count=2):
        if self._thread is None:
            self._thread = Thread(target=self._api_task, daemon=True)
            self._thread.start()

        self._queue.put((callback, url, params, retry_count))

    def _api_call(
        self,
        url,
        params=None,
        retry_count=2,
        timeout=20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
    ):
        return self.request(
            f"{self.get_api_url()}/{url}",
            json.dumps(params, separators=(",", ":")
                       ) if params is not None else None,
            retry_count,
            timeout,
            deadline=deadline,
            redact_response=redact_response,
            on_dispatch=on_dispatch,
        )

    def get_api_url(self) -> str:
        return f"https://{self._country}{self._strings[0]}:{self._strings[1]}"

    @property
    def device_id(self) -> str:
        return self._did

    @property
    def dreame_cloud(self) -> bool:
        return True

    @property
    def object_name(self) -> str:
        return f"{self._model}/{self._uid}/{str(self._did)}/0"

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def connected(self) -> bool:
        return self._connected and self._client_connected

    def _reconnect_timer_cancel(self):
        if self._reconnect_timer is not None:
            self._reconnect_timer.cancel()
            del self._reconnect_timer
            self._reconnect_timer = None

    def _reconnect_timer_task(self):
        self._reconnect_timer_cancel()
        if self._client_connecting and self._client_connected:
            self._client_connected = False
            _LOGGER.warn("Device client reconnect failed! Retrying...")

    def _set_client_key(self) -> bool:
        if self._client_key != self._key:
            self._client_key = self._key
            self._client.username_pw_set(self._uuid, self._client_key)
            return True
        return False

    @staticmethod
    def _on_client_connect(client, self, flags, rc):
        self._client_connecting = False
        self._reconnect_timer_cancel()
        if rc == 0:
            if not self._client_connected:
                self._client_connected = True
                _LOGGER.debug("Connected to the device client")
            client.subscribe(
                f"/{self._strings[7]}/{self._did}/{self._uid}/{self._model}/{self._country}/")
            if self._connected_callback:
                try:
                    self._connected_callback()
                except:
                    pass
        else:
            _LOGGER.warn("Device client connection failed: %s", rc)
            if not self._set_client_key():
                self._client_connected = False

    @staticmethod
    def _on_client_disconnect(client, self, rc):
        if rc != 0 and not self._set_client_key():
            if rc == 5 and self._key_expire:
                self.login()
            if self._client_connected:
                if not self._client_connecting:
                    self._client_connecting = True
                    _LOGGER.info(
                        "Device Client disconnected (%s) Reconnecting...", rc)
                self._reconnect_timer_cancel()
                self._reconnect_timer = Timer(10, self._reconnect_timer_task)
                self._reconnect_timer.start()

    @staticmethod
    def _on_client_message(client, self, message):
        if self._message_callback:
            try:
                _LOGGER.debug("Message received: %s",
                              message.payload.decode("utf-8"))
                response = json.loads(message.payload.decode("utf-8"))
                if "data" in response and response["data"]:
                    self._message_callback(response["data"])
            except:
                _LOGGER.error("Message: can't decode: %s")
                pass

    @staticmethod
    def get_random_agent_id() -> str:
        letters = "ABCDEF"
        result_str = "".join(random.choice(letters) for i in range(13))
        return result_str

    def _handle_device_info(self, info):
        self._uid = info[self._strings[8]]
        self._did = info["did"]
        self._model = info[self._strings[35]]
        self._host = info[self._strings[9]]
        prop = info[self._strings[10]]
        if prop and prop != "":
            prop = json.loads(prop)
            if self._strings[11] in prop:
                self._stream_key = prop[self._strings[11]]

    def connect(self, message_callback=None, connected_callback=None):
        with self._operation_lock():
            return self._connect_unlocked(message_callback, connected_callback)

    def _connect_unlocked(self, message_callback=None, connected_callback=None):
        if self._logged_in:
            info = self.get_device_info()
            if info:
                if message_callback:
                    self._message_callback = message_callback
                    self._connected_callback = connected_callback
                    if self._client is None:
                        _LOGGER.debug("Connecting to the device client")
                        try:
                            host = self._host.split(":")
                            self._client = mqtt_client.Client(
                                mqtt_client.CallbackAPIVersion.VERSION1,
                                f"{self._strings[53]}{self._uid}{self._strings[54]}{DreameMowerDreameHomeCloudProtocol.get_random_agent_id()}{self._strings[54]}{host[0]}",
                                clean_session=True,
                                userdata=self,
                            )
                            self._client.on_connect = DreameMowerDreameHomeCloudProtocol._on_client_connect
                            self._client.on_disconnect = DreameMowerDreameHomeCloudProtocol._on_client_disconnect
                            self._client.on_message = DreameMowerDreameHomeCloudProtocol._on_client_message
                            self._client.reconnect_delay_set(1, 15)
                            self._client.tls_set_context(
                                create_cloud_mqtt_ssl_context()
                            )
                            self._client.tls_insecure_set(False)
                            self._set_client_key()
                            self._client.connect(host[0], int(host[1]), 50)
                            self._client.loop_start()
                        except Exception as e:
                            _LOGGER.error("Connect failed. error: %s", e)
                            pass
                    elif not self._client_connected:
                        _LOGGER.error("Not connected to the device client")
                        self._set_client_key()
                self._connected = True
                return info
        return None

    def login(
        self,
        timeout: float = 10,
        *,
        deadline: float | None = None,
    ) -> bool:
        with self._operation_lock():
            return self._login_unlocked(timeout, deadline=deadline)

    def _login_unlocked(
        self,
        timeout: float = 10,
        *,
        deadline: float | None = None,
    ) -> bool:
        self._session.close()
        self._session = requests.session()
        self._logged_in = False

        if self._strings is None:
            if self._account_type == "dreame":
                self._strings = json.loads(zlib.decompress(
                    base64.b64decode(DREAME_STRINGS), zlib.MAX_WBITS | 32))
            if self._account_type == "mova":
                self._strings = json.loads(zlib.decompress(
                    base64.b64decode(MOVA_STRINGS), zlib.MAX_WBITS | 32))

        try:
            if self._secondary_key:
                data = f"{self._strings[12]}{self._strings[13]}{self._secondary_key}"
            else:
                data = f"{self._strings[12]}{self._strings[14]}{self._username}{self._strings[15]}{hashlib.md5((self._password + self._strings[2]).encode('utf-8')).hexdigest()}{self._strings[16]}"

            headers = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Language": "en-US;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                self._strings[47]: self._strings[3],
                self._strings[49]: self._strings[5],
                self._strings[50]: self._ti if self._ti else self._strings[6],
            }

            if self._country == "cn":
                headers[self._strings[48]] = self._strings[4]

            request_timeout = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise requests.exceptions.Timeout(
                        "The cloud login timed out."
                    )
                request_timeout = min(request_timeout, remaining)
            request_options = {
                "headers": headers,
                "data": data,
                "timeout": request_timeout,
            }
            if deadline is not None:
                request_options["stream"] = True
            response = _post_cloud_response(
                self._session,
                self.get_api_url() + self._strings[17],
                request_options,
                deadline=deadline,
            )
            if deadline is not None:
                try:
                    response_text = _read_cloud_response_text_with_deadline(
                        response,
                        deadline=deadline,
                    )
                finally:
                    response.close()
            else:
                response_text = response.text
            if response.status_code == 200:
                data = json.loads(response_text)
                if self._strings[18] in data:
                    self._key = data.get(self._strings[18])
                    self._secondary_key = data.get(self._strings[19])
                    self._key_expire = time.time(
                    ) + data.get(self._strings[20]) - 120
                    self._logged_in = True
                    self._uuid = data.get("uid")
                    self._location = data.get(
                        self._strings[21], self._location)
                    self._ti = data.get(self._strings[22], self._ti)
            else:
                try:
                    data = json.loads(response_text)
                    if "error_description" in data and "refresh token" in data["error_description"]:
                        self._secondary_key = None
                        return self.login(timeout=timeout, deadline=deadline)
                except:
                    pass
                _LOGGER.error("Login failed: %s => %s -- %s -- %s", response_text,
                              self.get_api_url() + self._strings[17], headers, data)
        except requests.exceptions.Timeout:
            response = None
            _LOGGER.warning(
                "Login Failed: Read timed out. (read timeout=%s)",
                timeout,
            )
        except Exception as ex:
            response = None
            _LOGGER.error("Login failed: %s", str(ex))

        if self._logged_in:
            self._fail_count = 0
            self._connected = True
        return self._logged_in

    def get_devices(
        self,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
    ) -> Any:
        response = self._api_call(
            f"{self._strings[23]}/{self._strings[24]}/{self._strings[27]}/{self._strings[28]}",
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
        )
        _LOGGER.debug(
            "DreameMowerDreameHomeCloudProtocol.get_devices %s", response)
        if response:
            if "data" in response and response["code"] == 0:
                return response["data"]
        return None

    def get_device_list_v2(
        self,
        current: int = 1,
        size: int = 20,
        lang: str | None = None,
        master: bool | None = None,
        shared_status: int | None = None,
    ) -> Any:
        params = {
            "current": current,
            "size": size,
        }
        if lang:
            params["lang"] = lang
        if master is not None:
            params["master"] = master
        if shared_status is not None:
            params["sharedStatus"] = shared_status

        response = self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotuserbind/device/listV2",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response["code"] == 0:
            data = response["data"]
            if "page" in data:
                return data["page"]
            return data
        return None

    def get_device_info_v2(
        self,
        lang: str | None = None,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
    ) -> Any:
        params = {"did": self._did}
        if lang:
            params["lang"] = lang

        response = self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotuserbind/device/info",
            json.dumps(params, separators=(",", ":")),
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
        )
        if response and "data" in response and response["code"] == 0:
            data = response["data"]
            self._handle_device_info(data)
            return data
        return None

    def get_user_features(self, lang: str | None = None) -> Any:
        params = {"did": self._did}
        if lang:
            params["lang"] = lang

        response = self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotuserbind/queryDevicePermit",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response["code"] == 0:
            return response["data"]
        return None

    def check_device_version(self, lang: str | None = None) -> Any:
        params = {"did": self._did}
        if lang:
            params["lang"] = lang

        response = self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotuserbind/checkDeviceVersion",
            json.dumps(params, separators=(",", ":")),
        )
        if response and response.get("code") == 0 and "data" in response:
            return response["data"]
        return response

    def manual_firmware_update(self, lang: str | None = None) -> Any:
        params = {"did": self._did}
        if lang:
            params["lang"] = lang

        return self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotuserbind/manualFirmwareUpdate",
            json.dumps(params, separators=(",", ":")),
        )

    def get_device_otc_info(self, lang: str | None = None) -> Any:
        params = {"did": self._did}
        if lang:
            params["lang"] = lang

        response = self.request(
            f"{self.get_api_url()}/dreame-user-iot/iotstatus/devOTCInfo",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response["code"] == 0:
            return response["data"]
        return None

    def get_tx_video_access_token(self, os: int = 1) -> Any:
        response = self.request(
            f"{self.get_api_url()}/dreame-third-video/tx/user/accesstoken",
            json.dumps({"os": os}, separators=(",", ":")),
        )
        if response and "data" in response and response.get("code", 0) == 0:
            return response["data"]
        return response

    def get_tx_video_device_identity(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> Any:
        if not self._uid:
            self.get_device_info_v2()
        params = {"did": self._did, "os": os}
        if access_token:
            params["accesstoken"] = access_token
            params["accessToken"] = access_token
        if self._uid:
            params["uid"] = str(self._uid)
        if self._model:
            params["model"] = self._model

        response = self.request(
            f"{self.get_api_url()}/dreame-third-video/tx/mgr/dev/getIdentity",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response.get("code", 0) == 0:
            return response["data"]
        return response

    def pair_tx_video_device(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> Any:
        if not self._uid or not self._model:
            self.get_device_info_v2()
        params = {"did": self._did, "os": os}
        if access_token:
            params["accesstoken"] = access_token
        if self._uid:
            params["uid"] = str(self._uid)
        if self._model:
            params["model"] = self._model

        response = self.request(
            f"{self.get_api_url()}/dreame-third-video/tx/dev/pair",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response["code"] == 0:
            return response["data"]
        return response

    def get_tx_video_user_eligibility(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> Any:
        """Return the read-only TX video eligibility response for this device."""
        params = {"did": self._did, "os": os}
        if access_token:
            params["accesstoken"] = access_token
            params["accessToken"] = access_token
        response = self.request(
            f"{self.get_api_url()}/dreame-third-video/tx/dev/isDevUser",
            json.dumps(params, separators=(",", ":")),
            retry_count=0,
            timeout=5,
        )
        if response and "data" in response and response.get("code", 0) == 0:
            return response["data"]
        return response

    def get_tx_video_p2p_info(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> Any:
        params = {"did": self._did, "os": os}
        if access_token:
            params["accesstoken"] = access_token
            params["accessToken"] = access_token
        response = self.request(
            f"{self.get_api_url()}/dreame-third-video/tx/dev/getP2PInfo",
            json.dumps(params, separators=(",", ":")),
        )
        if response and "data" in response and response.get("code", 0) == 0:
            return response["data"]
        return response

    def get_app_plugin_version(
        self,
        model: str | None = None,
        app_version_code: int = 2050300,
        os: int = 1,
    ) -> Any:
        params = {
            "model": model or self._model,
            "appVer": app_version_code,
            "os": os,
        }

        response = self.get(
            f"{self.get_api_url()}/dreame-product/upgrades/appplugin",
            params=params,
        )
        if response and "data" in response and response["code"] == 0:
            return response["data"]
        return None

    def get_device_info(
        self,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
    ) -> Any:
        response = self._api_call(
            f"{self._strings[23]}/{self._strings[24]}/{self._strings[27]}/{self._strings[29]}",
            {"did": self._did},
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
        )
        if response and "data" in response and response["code"] == 0:
            data = response["data"]
            self._handle_device_info(data)
            response = self._api_call(
                f"{self._strings[23]}/{self._strings[25]}/{self._strings[30]}",
                {"did": self._did},
                retry_count=retry_count,
                timeout=timeout,
                deadline=deadline,
            )
            if response and "data" in response and response["code"] == 0:
                if self._strings[31] in response["data"]:
                    data = {
                        **response["data"][self._strings[31]][self._strings[32]],
                        **data,
                    }
                else:
                    _LOGGER.debug(
                        "Get Device OTC Info Retrying with fallback... (%s)", response)
                    devices = self.get_devices(
                        retry_count=retry_count,
                        timeout=timeout,
                        deadline=deadline,
                    )
                    if devices is not None:
                        found = list(
                            filter(
                                lambda d: str(d["did"]) == self._did,
                                devices[self._strings[34]][self._strings[36]],
                            )
                        )
                        if len(found) > 0:
                            self._handle_device_info(found[0])
                            return found[0]
                    _LOGGER.error("Get Device OTC Info Failed!")
                    return None
            return data
        return None

    def get_info(self, mac: str) -> Tuple[Optional[str], Optional[str]]:
        if self._did is not None:
            return " ", self._host
        devices = self.get_devices()
        if devices is not None:
            found = list(
                filter(
                    lambda d: str(d["mac"]) == mac,
                    devices[self._strings[34]][self._strings[36]],
                )
            )
            if len(found) > 0:
                self._handle_device_info(found[0])
                return " ", self._host
        return None, None

    def send_async(self, callback, method, parameters, retry_count: int = 2):
        with self._operation_lock():
            return self._send_async_unlocked(
                callback,
                method,
                parameters,
                retry_count,
            )

    def _send_async_unlocked(
        self,
        callback,
        method,
        parameters,
        retry_count: int = 2,
    ):
        host = ""
        if self._host and len(self._host):
            host = f"-{self._host.split('.')[0]}"

        self._id = self._id + 1
        self._api_call_async(
            lambda api_response: callback(
                None
                if api_response is None or "data" not in api_response or "result" not in api_response["data"]
                else api_response["data"]["result"]
            ),
            f"{self._strings[37]}{host}/{self._strings[27]}/{self._strings[38]}",
            {
                "did": str(self._did),
                "id": self._id,
                "data": {
                    "did": str(self._did),
                    "id": self._id,
                    "method": method,
                    "params": parameters,
                },
            },
            retry_count,
        )

    def send(
        self,
        method,
        parameters,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
        raise_on_api_error: bool = False,
    ) -> Any:
        with self._operation_lock():
            return self._send_unlocked(
                method,
                parameters,
                retry_count,
                timeout,
                deadline=deadline,
                redact_response=redact_response,
                on_dispatch=on_dispatch,
                raise_on_api_error=raise_on_api_error,
            )

    def _send_unlocked(
        self,
        method,
        parameters,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
        raise_on_api_error: bool = False,
    ) -> Any:
        host = ""
        if self._host and len(self._host):
            host = f"-{self._host.split('.')[0]}"

        api_response = self._api_call(
            f"{self._strings[37]}{host}/{self._strings[27]}/{self._strings[38]}",
            {
                "did": str(self._did),
                "id": self._id,
                "data": {
                    "did": str(self._did),
                    "id": self._id,
                    "method": method,
                    "params": parameters,
                },
            },
            retry_count,
            timeout,
            deadline=deadline,
            redact_response=redact_response,
            on_dispatch=on_dispatch,
        )
        logged_response = _app_action_response_log_value(
            api_response,
            redact=redact_response,
        )
        _LOGGER.debug(
            "DreameMowerDreameHomeCloudProtocol.send api_response: %s",
            logged_response,
        )
        self._id = self._id + 1
        response_code = (
            api_response.get("code") if isinstance(api_response, Mapping) else None
        )
        if (
            raise_on_api_error
            and isinstance(response_code, int)
            and not isinstance(response_code, bool)
            and response_code != 0
        ):
            raise DreameLawnMowerCloudAPIError(response_code)
        if isinstance(api_response, Mapping) and api_response.get("code") == 80001:
            # Seems to be a valid error message from the server which translates to:
            #   "The device may be offline and the command sending timed out."
            # While the time out was a return value from the server, implying that the
            # the server correctly handled the request.
            _LOGGER.debug(
                "DreameMowerDreameHomeCloudProtocol.send 80001, return none: %s",
                logged_response,
            )
            return None

        if (
            isinstance(api_response, Mapping)
            and api_response.get("code") == 0
            and api_response.get("success") is True
            and api_response.get("data") in ("", None)
        ):
            _LOGGER.debug(
                "DreameMowerDreameHomeCloudProtocol.send accepted without result: %s",
                logged_response,
            )
            return None

        if (
            api_response is None
            or not isinstance(api_response, Mapping)
            or "data" not in api_response
            or not isinstance(api_response["data"], Mapping)
            or "result" not in api_response["data"]
        ):
            _LOGGER.warning(
                "DreameMowerDreameHomeCloudProtocol.send failed: %s",
                logged_response,
            )
            return None
        return api_response["data"]["result"]

    def call_app_action(
        self,
        payload: Mapping[str, Any],
        siid: int = 2,
        aiid: int = 50,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
        raise_on_api_error: bool = False,
    ) -> Any:
        """Call the mobile-app action bridge used by mower plugin commands."""
        return self.send(
            "action",
            {
                "did": str(self._did),
                "siid": siid,
                "aiid": aiid,
                "in": [payload],
            },
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
            redact_response=redact_response,
            on_dispatch=on_dispatch,
            raise_on_api_error=raise_on_api_error,
        )

    def get_file(self, url: str, retry_count: int = 4) -> Any:
        retries = 0
        if not retry_count or retry_count < 0:
            retry_count = 0
        while retries < retry_count + 1:
            try:
                response = self._session.get(url, timeout=6)
            except Exception as ex:
                response = None
                _LOGGER.warning("Unable to get file at %s: %s", url, ex)
            if response is not None and response.status_code == 200:
                return response.content
            retries = retries + 1
        return None

    def get_file_url(self, object_name: str = "") -> Any:
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[39]}/{self._strings[56]}",
            {
                "did": str(self._did),
                "uid": str(self._uid),
                self._strings[35]: self._model,
                "filename": object_name[1:],
                self._strings[21]: self._country,
            },
        )
        if api_response is None or "data" not in api_response:
            return None

        return api_response["data"]

    def get_interim_file_url(
        self,
        object_name: str = "",
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
    ) -> str:
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[39]}/{self._strings[55]}",
            {
                "did": str(self._did),
                self._strings[35]: self._model,
                self._strings[40]: object_name,
                self._strings[21]: self._country,
            },
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
        )
        if api_response is None or "data" not in api_response:
            return None

        return api_response["data"]

    def get_properties(
        self,
        keys,
        retry_count: int = 2,
        timeout: float = 20,
        *,
        deadline: float | None = None,
    ):
        params = {"did": str(self._did), "keys": keys}
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[25]}/{self._strings[41]}",
            params,
            retry_count=retry_count,
            timeout=timeout,
            deadline=deadline,
        )
        if api_response is None or "data" not in api_response:
            return None

        return api_response["data"]

    def get_device_property(self, key, limit=1, time_start=0, time_end=9999999999):
        return self.get_device_data(key, "prop", limit, time_start, time_end)

    def get_device_event(self, key, limit=1, time_start=0, time_end=9999999999):
        return self.get_device_data(key, "event", limit, time_start, time_end)

    def get_device_data(self, key, type, limit=1, time_start=0, time_end=9999999999):
        data_keys = key.split(".")
        params = {
            "uid": str(self._uid),
            "did": str(self._did),
            "from": time_start if time_start else 1687019188,
            "limit": limit,
            "siid": data_keys[0],
            self._strings[21]: self._country,
            self._strings[42]: 3,
        }
        param_name = "piid"
        if type == "event":
            param_name = "eiid"
        elif type == "action":
            param_name = "aiid"

        params[param_name] = data_keys[1]
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[25]}/{self._strings[43]}", params)
        if api_response is None or "data" not in api_response or self._strings[33] not in api_response["data"]:
            return None

        return api_response["data"][self._strings[33]]

    def get_batch_device_datas(self, props) -> Any:
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[26]}/{self._strings[44]}",
            {"did": self._did, self._strings[35]: props},
        )
        if api_response is None or "data" not in api_response:
            return None
        return api_response["data"]

    def set_batch_device_datas(self, props) -> Any:
        api_response = self._api_call(
            f"{self._strings[23]}/{self._strings[26]}/{self._strings[45]}",
            {"did": self._did, self._strings[35]: props},
        )
        if api_response is None or "result" not in api_response:
            return None
        return api_response["result"]

    def request(
        self,
        url: str,
        data,
        retry_count=2,
        timeout=20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
    ) -> Any:
        with self._operation_lock():
            return self._request_unlocked(
                url,
                data,
                retry_count,
                timeout,
                deadline=deadline,
                redact_response=redact_response,
                on_dispatch=on_dispatch,
            )

    def _request_unlocked(
        self,
        url: str,
        data,
        retry_count=2,
        timeout=20,
        *,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
    ) -> Any:
        _LOGGER.debug(
            "DreameMowerDreameHomeCloudProtocol.request %s %s",
            url,
            _cloud_request_log_value(url, data),
        )

        retries = 0
        if not retry_count or retry_count < 0:
            retry_count = 0
        response_text = None
        while retries < retry_count + 1:
            try:
                if self._key_expire and time.time() > self._key_expire:
                    if deadline is None:
                        self.login()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise requests.exceptions.Timeout(
                                "The cloud response timed out."
                            )
                        if not self.login(
                            timeout=min(timeout, remaining),
                            deadline=deadline,
                        ):
                            raise requests.exceptions.Timeout(
                                "The cloud login did not complete before "
                                "the response deadline."
                            )

                headers = {
                    "Accept": "*/*",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept-Language": "en-US;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                    self._strings[47]: self._strings[3],
                    self._strings[49]: self._strings[5],
                    self._strings[50]: self._ti if self._ti else self._strings[6],
                    self._strings[51]: self._strings[52],
                    self._strings[46]: self._key,
                }
                if self._country == "cn":
                    headers[self._strings[48]] = self._strings[4]

                request_options = {
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                }
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise requests.exceptions.Timeout(
                            "The cloud response timed out."
                        )
                    request_options["timeout"] = min(timeout, remaining)
                    request_options["stream"] = True
                post_options: dict[str, Any] = {"deadline": deadline}
                if on_dispatch is not None:
                    post_options["on_dispatch"] = on_dispatch
                response = _post_cloud_response(
                    self._session,
                    url,
                    request_options,
                    **post_options,
                )
                if deadline is not None:
                    try:
                        response_text = _read_cloud_response_text_with_deadline(
                            response,
                            deadline=deadline,
                        )
                    finally:
                        response.close()
                else:
                    response_text = response.text
                break
            except requests.exceptions.Timeout:
                retries = retries + 1
                response = None
                if self._connected:
                    _LOGGER.warning(
                        "DreameMowerDreameHomeCloudProtocol.request: Read timed out. (read timeout=%s): %s",
                        timeout,
                        _cloud_request_log_value(url, data),
                    )
            except Exception as ex:
                retries = retries + 1
                response = None
                if self._connected:
                    _LOGGER.warning(
                        "Error while executing request: %s", str(ex))

        _LOGGER.debug(
            "DreameMowerDreameHomeCloudProtocol.request response: %s", response)
        if response is not None:
            if response.status_code == 200:
                self._fail_count = 0
                self._connected = True
                _LOGGER.debug(
                    "DreameMowerDreameHomeCloudProtocol.request response.text: %s",
                    _app_action_response_log_value(
                        _cloud_request_log_value(url, response_text),
                        redact=redact_response,
                    ),
                )
                return json.loads(response_text)
            elif response.status_code == 401 and self._secondary_key:
                _LOGGER.debug("Execute api call failed: Token Expired")
                if deadline is None:
                    self.login()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self.login(
                            timeout=min(timeout, remaining),
                            deadline=deadline,
                        )
            else:
                _LOGGER.warn(
                    "Execute api call failed with response: %s",
                    _app_action_response_log_value(
                        _cloud_request_log_value(url, response_text),
                        redact=redact_response,
                    ),
                )

        if self._fail_count == 5:
            self._connected = False
        else:
            self._fail_count = self._fail_count + 1
        return None

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        retry_count=2,
    ) -> Any:
        with self._operation_lock():
            return self._get_unlocked(url, params, retry_count)

    def _get_unlocked(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        retry_count=2,
    ) -> Any:
        _LOGGER.debug("DreameMowerDreameHomeCloudProtocol.get %s %s", url, params)

        retries = 0
        if not retry_count or retry_count < 0:
            retry_count = 0
        while retries < retry_count + 1:
            timeout = 20
            try:
                if self._key_expire and time.time() > self._key_expire:
                    self.login()

                headers = {
                    "Accept": "*/*",
                    "Accept-Language": "en-US;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                    self._strings[47]: self._strings[3],
                    self._strings[49]: self._strings[5],
                    self._strings[50]: self._ti if self._ti else self._strings[6],
                    self._strings[51]: self._strings[52],
                    self._strings[46]: self._key,
                }
                if self._country == "cn":
                    headers[self._strings[48]] = self._strings[4]

                response = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=timeout,
                )
                break
            except requests.exceptions.Timeout:
                retries = retries + 1
                response = None
                if self._connected:
                    _LOGGER.warning(
                        "DreameMowerDreameHomeCloudProtocol.get: Read timed out. "
                        "(read timeout=%s): %s",
                        timeout,
                        params,
                    )
            except Exception as ex:
                retries = retries + 1
                response = None
                if self._connected:
                    _LOGGER.warning("Error while executing get request: %s", str(ex))

        if response is not None:
            if response.status_code == 200:
                self._fail_count = 0
                self._connected = True
                _LOGGER.debug(
                    "DreameMowerDreameHomeCloudProtocol.get response.text: %s",
                    response.text,
                )
                return json.loads(response.text)
            if response.status_code == 401 and self._secondary_key:
                _LOGGER.debug("Execute get request failed: Token Expired")
                self.login()
            else:
                _LOGGER.warn(
                    "Execute get request failed with response: %s", response.text)

        if self._fail_count == 5:
            self._connected = False
        else:
            self._fail_count = self._fail_count + 1
        return None

    def disconnect(self):
        with self._operation_lock():
            self._disconnect_unlocked()

    def _disconnect_unlocked(self):
        self._session.close()
        self._connected = False
        self._logged_in = False
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._client_connected = False
            self._client_connecting = False
        if self._thread:
            self._queue.put([])
        self._message_callback = None
        self._connected_callback = None
