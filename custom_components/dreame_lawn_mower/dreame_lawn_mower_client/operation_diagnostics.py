"""Privacy-safe diagnostics for staged cloud operations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_MISSING = object()
_MAX_DEPTH = 5
_MAX_FIELDS = 40
_MAX_ITEMS = 4
_MAX_MESSAGES = 4
_MAX_MESSAGE_LENGTH = 240

_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "appid",
    "appkey",
    "appsecret",
    "authorization",
    "clientid",
    "clientsecret",
    "cookie",
    "deviceid",
    "devicename",
    "did",
    "familyid",
    "host",
    "p2pinfo",
    "refreshtoken",
    "secretid",
    "secretkey",
    "serialnumber",
    "sessiontoken",
    "signature",
    "sn",
    "token",
    "uid",
    "username",
    "uuid",
    "xp2pinfo",
    "xp2pkey",
    "xp2psecretkey",
}
_CODE_KEYS = {
    "code",
    "errorcode",
    "errno",
    "statuscode",
}
_MESSAGE_KEYS = {
    "detail",
    "error",
    "errormessage",
    "message",
    "msg",
    "reason",
}
_ASSIGNED_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(access[_-]?token|app[_-]?(?:id|key|secret)|client[_-]?(?:id|secret)|"
    r"device[_-]?(?:id|name)|did|family[_-]?id|p2p[_-]?info|refresh[_-]?token|"
    r"secret[_-]?(?:id|key)|serial[_-]?number|session[_-]?token|sn|token|uid|"
    r"username|uuid|xp2p[_-]?(?:info|key|secretkey))\b"
    r"([\"']?)(\s*(?:[:=]\s*|\s+))([\"']?)[^\s,;&\"'}]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URL_VALUE = re.compile(r"(?i)\bhttps?://[^\s,;\"']+")
_EMAIL_VALUE = re.compile(r"(?i)\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_LONG_INTEGER = re.compile(r"\b\d{6,}\b")
_TOKEN_LIKE_VALUE = re.compile(r"\b[A-Za-z0-9_+/=-]{24,}\b")


def build_operation_stage_diagnostics(
    stage: str,
    *,
    request_context: Mapping[str, Any] | None = None,
    response: Any = _MISSING,
    result: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
    sensitive_values: Sequence[object] = (),
) -> dict[str, Any]:
    """Return one bounded stage summary without retaining response values."""
    payload: dict[str, Any] = {"stage": _safe_local_text(stage)}
    if request_context:
        payload["request"] = {
            _safe_field_name(key, index): _safe_context_value(value)
            for index, (key, value) in enumerate(
                list(request_context.items())[:_MAX_FIELDS]
            )
        }
    if response is not _MISSING:
        payload["response"] = summarize_operation_response(
            response,
            sensitive_values=sensitive_values,
        )
    if result:
        payload["result"] = {
            _safe_field_name(key, index): _safe_context_value(value)
            for index, (key, value) in enumerate(list(result.items())[:_MAX_FIELDS])
        }
    if error is not None:
        redactions = _sensitive_text_values(response, sensitive_values)
        payload["error"] = {
            "type": type(error).__name__,
            "message": _sanitize_message(str(error), redactions),
        }
    return payload


def summarize_operation_response(
    value: Any,
    *,
    sensitive_values: Sequence[object] = (),
) -> dict[str, Any]:
    """Return response shape, safe status codes, and sanitized messages."""
    redactions = _sensitive_text_values(value, sensitive_values)
    summary: dict[str, Any] = {
        "type": _value_type(value),
        "empty": _is_empty(value),
        "shape": _response_shape(value, depth=0),
    }
    codes = _named_scalar_values(value, _CODE_KEYS, include_text=False)
    if codes:
        summary["codes"] = codes
    messages = _named_scalar_values(value, _MESSAGE_KEYS, include_text=True)
    if messages:
        summary["messages"] = [
            {
                "path": item["path"],
                "text": _sanitize_message(str(item["value"]), redactions),
            }
            for item in messages[:_MAX_MESSAGES]
        ]
    return summary


def _response_shape(value: Any, *, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
        return {"type": _value_type(value), "truncated": True}
    if isinstance(value, Mapping):
        items = list(value.items())
        fields = [
            {
                "name": _safe_field_name(key, index),
                "shape": _response_shape(item, depth=depth + 1),
            }
            for index, (key, item) in enumerate(items[:_MAX_FIELDS])
        ]
        shape: dict[str, Any] = {
            "type": "object",
            "field_count": len(items),
            "fields": fields,
        }
        if len(items) > _MAX_FIELDS:
            shape["truncated"] = True
        return shape
    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        items = list(value)
        shape = {
            "type": "array",
            "item_count": len(items),
            "items": [
                _response_shape(item, depth=depth + 1)
                for item in items[:_MAX_ITEMS]
            ],
        }
        if len(items) > _MAX_ITEMS:
            shape["truncated"] = True
        return shape
    return {"type": _value_type(value)}


def _named_scalar_values(
    value: Any,
    wanted_keys: set[str],
    *,
    include_text: bool,
    path: str = "$",
    depth: int = 0,
) -> list[dict[str, Any]]:
    if depth >= _MAX_DEPTH:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(list(value.items())[:_MAX_FIELDS]):
            safe_key = _safe_field_name(key, index)
            item_path = f"{path}.{safe_key}"
            normalized_key = _normalized_key(key)
            if normalized_key in wanted_keys and _is_safe_scalar(item, include_text):
                found.append({"path": item_path, "value": item})
            found.extend(
                _named_scalar_values(
                    item,
                    wanted_keys,
                    include_text=include_text,
                    path=item_path,
                    depth=depth + 1,
                )
            )
    elif isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        for index, item in enumerate(list(value)[:_MAX_ITEMS]):
            found.extend(
                _named_scalar_values(
                    item,
                    wanted_keys,
                    include_text=include_text,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                )
            )
    return found


def _sensitive_text_values(
    value: Any,
    supplied: Sequence[object],
) -> tuple[str, ...]:
    values = {
        text
        for item in supplied
        if (text := _redaction_text(item)) is not None
    }

    def collect(item: Any, *, key: object = "", depth: int = 0) -> None:
        if depth >= _MAX_DEPTH:
            return
        if isinstance(item, Mapping):
            for child_key, child_value in list(item.items())[:_MAX_FIELDS]:
                if _normalized_key(child_key) in _SENSITIVE_KEYS:
                    text = _redaction_text(child_value)
                    if text is not None:
                        values.add(text)
                collect(child_value, key=child_key, depth=depth + 1)
        elif isinstance(item, Sequence) and not isinstance(
            item,
            str | bytes | bytearray,
        ):
            for child in list(item)[:_MAX_ITEMS]:
                collect(child, key=key, depth=depth + 1)

    collect(value)
    return tuple(sorted(values, key=len, reverse=True))


def _sanitize_message(value: str, redactions: Sequence[str]) -> str:
    text = value.replace("\r", " ").replace("\n", " ")
    for sensitive in redactions:
        text = text.replace(sensitive, "**REDACTED**")
    text = _ASSIGNED_SENSITIVE_VALUE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{match.group(4)}**REDACTED**"
        ),
        text,
    )
    text = _BEARER_VALUE.sub("Bearer **REDACTED**", text)
    text = _URL_VALUE.sub("**REDACTED_URL**", text)
    text = _EMAIL_VALUE.sub("**REDACTED_EMAIL**", text)
    text = _LONG_INTEGER.sub("**REDACTED_ID**", text)
    text = _TOKEN_LIKE_VALUE.sub("**REDACTED_VALUE**", text)
    if len(text) > _MAX_MESSAGE_LENGTH:
        return f"{text[: _MAX_MESSAGE_LENGTH - 3]}..."
    return text


def _safe_field_name(value: object, index: int) -> str:
    text = str(value)
    if _SAFE_KEY.fullmatch(text):
        return text
    return f"<dynamic-field-{index}>"


def _safe_local_text(value: object) -> str:
    text = str(value)
    return text if _SAFE_KEY.fullmatch(text) else "operation"


def _safe_context_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and _SAFE_KEY.fullmatch(value):
        return value
    return {"type": _value_type(value), "present": not _is_empty(value)}


def _is_safe_scalar(value: Any, include_text: bool) -> bool:
    if isinstance(value, bool | int | float) or value is None:
        return True
    return include_text and isinstance(value, str)


def _redaction_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value
    elif isinstance(value, int):
        text = str(value)
    else:
        return None
    return text if len(text) >= 3 else None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str | bytes | bytearray | Mapping | Sequence):
        return len(value) == 0
    return False


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        return "array"
    return type(value).__name__
