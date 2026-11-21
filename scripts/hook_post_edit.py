#!/usr/bin/env python3
"""PostToolUse hook: run the fast gates (those marked `on_edit`) after each file edit.

A failure is fed straight back to the agent, so lint and type errors are fixed while
the change is fresh instead of piling up for the Stop gate.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_lib as H  # noqa: E402


def main() -> int:
    if os.environ.get("HARNESS_DISABLE") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    root = H.find_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return 0
    tid = H.active_task(root)
    if not tid or H.load_state(root, tid).get("phase") != "implement":
        return 0
    ok, results = H.run_gates(root, only_on_edit=True)
    if ok:
        return 0
    failed = [r for r in results if not r["passed"]]
    reason = "Fast gates failed after this edit:\n\n" + "\n\n".join(
        f"[{r['name']}] exit {r['exit_code']}\n{r['output']}" for r in failed
    )
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason[:9000]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
