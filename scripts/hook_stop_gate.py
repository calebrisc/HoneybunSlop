#!/usr/bin/env python3
"""Stop / SubagentStop hook: an implementer may not finish until its work is proven.

While the active task is in the `implement` phase, stopping is blocked unless the
contract is valid, every acceptance criterion has fresh passing evidence in the ledger
(recorded by `harness.py record`, never typed by hand), and every configured gate passes.
The block reason is fed back to the agent so it can fix the gap.

To stop an agent from looping forever, blocks are capped per task (config
`max_stop_blocks`, default 3). Past the cap the task is marked `escalated` and the
agent is allowed to stop, with a message telling the person what is unproven.

Outside the implement phase, outside a harness project, or with HARNESS_DISABLE=1 the
hook does nothing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_lib as H  # noqa: E402
from harness import readiness  # noqa: E402

# Subagents whose stop is gated. Verifiers and the refuter only read, so they are not.
GATED_AGENT_HINTS = ("implementer",)


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def main() -> int:
    if os.environ.get("HARNESS_DISABLE") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    event = payload.get("hook_event_name", "Stop")
    if event == "SubagentStop":
        agent = str(payload.get("agent_type") or payload.get("subagent_type") or "")
        if not any(h in agent for h in GATED_AGENT_HINTS):
            return 0

    root = H.find_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return 0
    tid = H.active_task(root)
    if not tid:
        return 0
    state = H.load_state(root, tid)
    if state.get("phase") != "implement":
        return 0

    rep = readiness(root, tid)
    if rep.ok:
        return 0

    cfg = H.load_config(root)
    state["stop_blocks"] = int(state.get("stop_blocks", 0)) + 1
    H.save_state(root, tid, state)
    if state["stop_blocks"] > int(cfg["max_stop_blocks"]):
        H.set_phase(root, tid, "escalated", "stop gate block limit reached")
        _emit({
            "systemMessage": (
                f"Harness: task {tid} was escalated after {cfg['max_stop_blocks']} blocked stops. "
                "Unproven items:\n" + rep.render()
            )
        })
        return 0

    reason = (
        f"Task {tid} is not proven yet (stop attempt {state['stop_blocks']} of "
        f"{cfg['max_stop_blocks']}). Fix these, re-record evidence with "
        "`harness.py record <criterion>`, then stop again. Do not edit tests or the "
        "contract to make them pass.\n\n" + rep.render()
    )
    _emit({"decision": "block", "reason": reason[:9000]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
