"""Synchronous adapter for the shared preference write policy."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from typing import Any

from .preference_write_plan import (
    PreferenceCommand,
    PreferenceDelay,
    PreferenceWriteRequest,
    ReadPreferences,
)


def capture_preference_result(
    plan: Generator[PreferenceWriteRequest, Any, dict[str, Any]],
    result: list[dict[str, Any]],
) -> Generator[PreferenceWriteRequest, Any]:
    result.append((yield from plan))


def run_preference_write(
    plan: Generator[PreferenceWriteRequest, Any, dict[str, Any]],
    read: Callable[[int], dict[str, Any]],
    write: Callable[[Mapping[str, Any]], Any],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Preserve synchronous read, write and retry timing contracts."""
    result: list[dict[str, Any]] = []
    driver = capture_preference_result(plan, result)
    try:
        request = next(driver)
        while True:
            try:
                response: Any
                match request:
                    case ReadPreferences():
                        response = read(request.map_index)
                    case PreferenceCommand():
                        response = write(request.action)
                    case PreferenceDelay():
                        sleep(request.seconds)
                        response = None
            except Exception as error:
                request = driver.throw(error)
            else:
                request = driver.send(response)
    except StopIteration:
        return result[0]
    finally:
        driver.close()
