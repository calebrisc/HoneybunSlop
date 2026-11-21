#!/usr/bin/env python3
"""PreToolUse hook: during implementation, the prover may not touch its own yardstick.

Blocks Edit/Write/MultiEdit/NotebookEdit on the contract's `protected` globs (usually the
tests and fixtures that define done) and on the harness state files, and blocks Bash
commands that write those state files directly instead of going through `harness.py`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_lib as H  # noqa: E402

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
STATE_FILE_RE = re.compile(r"\.harness/(tasks/[^/\s]+/(contract|ledger|state)\.json|config\.json|active)")
WRITE_HINT_RE = re.compile(r"(>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\bpython3?\s+-c|\bperl\b|\btruncate\b)")


def deny(reason: str) -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


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

    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    if tool in EDIT_TOOLS:
        raw = tin.get("file_path") or tin.get("notebook_path") or ""
        if not raw:
            return 0
        path = Path(raw)
        if not path.is_absolute():
            path = Path(payload.get("cwd") or os.getcwd()) / path
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return 0
        if H.matches_any(rel, H.protected_globs(root, tid)):
            deny(
                f"{rel} is protected while task {tid} is being implemented. Tests, fixtures and "
                "harness state define what done means; change the code under test instead. If the "
                "contract itself is wrong, stop and say so, and a person will reopen the contract phase."
            )
        return 0

    if tool == "Bash":
        cmd = tin.get("command", "")
        if STATE_FILE_RE.search(cmd) and WRITE_HINT_RE.search(cmd) and "harness.py" not in cmd:
            deny("Harness state files can only change through harness.py (for example `harness.py record`).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
