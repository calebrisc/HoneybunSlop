#!/usr/bin/env python3
"""SessionStart hook: install a stable `harness` launcher and report the active task.

Plugin files move when the plugin updates, so skills and agents call the launcher at
~/.honeybun-slop/harness instead of a versioned path. The launcher is rewritten every
session to point at the current plugin copy.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import harness_lib as H  # noqa: E402


def main() -> int:
    home = Path(os.environ.get("HONEYBUN_HOME", Path.home() / ".honeybun-slop"))
    home.mkdir(parents=True, exist_ok=True)
    launcher = home / "harness"
    launcher.write_text(f'#!/bin/sh\nexec python3 "{SCRIPTS / "harness.py"}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    root = H.find_root(payload.get("cwd") or os.getcwd())
    lines = [f"Honeybun Slop harness launcher: {launcher}"]
    if root is not None:
        tid = H.active_task(root)
        if tid:
            st = H.load_state(root, tid)
            c = H.load_contract(root, tid) or {}
            lines.append(
                f"Active harness task: {tid} ({c.get('title', '')}), phase {st['phase']}, "
                f"round {st['round']}, rigor {c.get('rigor')}. Run `{launcher} status` for details."
            )
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
