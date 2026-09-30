"""Private one-shot runner for a verified component entry (do not import).

Launched by :mod:`agent_centric.cbp.component_process` as
``python -I _component_runner.py <component-dir> <module:qualname>``. It reads a
single JSON request from stdin, calls the component entry with the request's
payload, and writes a single JSON response to stdout. It is intentionally
standalone (stdlib only) so it never imports the harness.

Exit codes: 0 on success, 1 on a component error, 2 on a protocol/usage error.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from typing import Any, NoReturn

PROCESS_PROTOCOL = "component-process.v1"


def _emit(response: dict[str, Any], code: int) -> NoReturn:
    sys.stdout.write(json.dumps(response, sort_keys=True))
    sys.stdout.flush()
    sys.exit(code)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        _emit({"error": "usage: runner <component-dir> <module:qualname>"}, 2)
    component_dir, entrypoint = argv[1], argv[2]
    sys.path.insert(0, component_dir)

    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        _emit({"error": f"invalid request JSON: {exc}"}, 2)
    if not isinstance(request, dict) or request.get("protocol") != PROCESS_PROTOCOL:
        _emit({"error": "unsupported request protocol"}, 2)

    module_name, sep, qualname = entrypoint.partition(":")
    if not sep or not module_name or not qualname:
        _emit({"error": f"malformed entrypoint: {entrypoint!r}"}, 2)

    try:
        obj: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        if not callable(obj):
            raise TypeError(f"{entrypoint!r} is not callable")
        result = obj(request.get("payload"))
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        _emit({"error": "component raised"}, 1)
    _emit({"result": result}, 0)


if __name__ == "__main__":
    main(sys.argv)
