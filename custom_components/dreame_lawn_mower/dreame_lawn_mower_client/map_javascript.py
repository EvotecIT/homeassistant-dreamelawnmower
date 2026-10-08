"""Execute the embedded map optimizer without retaining threads or JS contexts."""

import base64
import json
from typing import Any

# quickjs-ng exposes this compiled module without Python type declarations.
from _quickjs import Context  # type: ignore[import-not-found]

from .resources import MAP_OPTIMIZER_JS

_OPTIMIZER_SOURCE = base64.b64decode(MAP_OPTIMIZER_JS).decode("utf-8")


def optimize_map(
    data: list[list[int]], dimensions: list[float],
    saved_data: list[list[int]] | None, saved_dimensions: list[float] | None,
    charger: list[float | None] | None,
) -> Any:
    # The quickjs.Function wrapper starts a global executor even on import.
    # Use its native context directly, keeping creation, execution and disposal
    # on the calling camera/map worker without sharing contexts across threads.
    context = Context()
    # Alpine workers can have a 128 KiB C stack. This iterative algorithm needs
    # little stack; keep QuickJS's overflow guard below that native limit.
    context.set_max_stack_size(64 * 1024)
    context.eval(_OPTIMIZER_SOURCE)
    arguments = [
        context.parse_json(json.dumps(value))
        for value in (data, dimensions, saved_data, saved_dimensions, charger)
    ]
    result = context.get("optimize")(*arguments)
    return json.loads(result.json())
