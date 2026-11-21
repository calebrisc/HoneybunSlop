#!/usr/bin/env python3
"""Command-line entry point for the Honeybun Slop harness.

Agents and people call this to move a task through its phases. Each transition is
guarded by a deterministic check, so a phase cannot be skipped by just saying so.

    harness.py init
    harness.py new <task-id> --title "..." [--rigor standard]
    harness.py status
    harness.py check-contract
    harness.py phase <phase> [--note "..."]
    harness.py record <criterion-id> [--note "..."]
    harness.py check-ledger [--rerun]
    harness.py gate [--on-edit]
    harness.py ready            # what the Stop hook checks before an implementer may stop
    harness.py finish
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_lib as H  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _root_or_die() -> Path:
    root = H.find_root()
    if root is None:
        sys.exit("No .harness/ directory found. Run `harness.py init` at the project root first.")
    return root


def _task_or_die(root: Path, task_id: str | None) -> str:
    tid = task_id or H.active_task(root)
    if not tid:
        sys.exit("No active task. Start one with `harness.py new <task-id> --title ...`.")
    if not H.task_dir(root, tid).is_dir():
        sys.exit(f"Task {tid!r} does not exist.")
    return tid


def readiness(root: Path, tid: str) -> H.Report:
    """Contract valid, every criterion has fresh passing evidence, and all gates pass."""
    rep = H.Report()
    c = H.check_contract(H.load_contract(root, tid))
    rep.errors += [f"contract: {e}" for e in c.errors]
    if c.ok:
        lg = H.check_ledger(root, tid)
        rep.errors += [f"ledger: {e}" for e in lg.errors]
        rep.warnings += [f"ledger: {w}" for w in lg.warnings]
    ok, gates = H.run_gates(root)
    for g in gates:
        if not g["passed"]:
            rep.errors.append(f"gate '{g['name']}' failed (exit {g['exit_code']}):\n{g['output']}")
    return rep


def cmd_init(args) -> int:
    root = Path.cwd()
    hdir = root / H.HARNESS_DIR
    (hdir / "tasks").mkdir(parents=True, exist_ok=True)
    cfg = hdir / "config.json"
    if not cfg.exists():
        shutil.copy(PLUGIN_ROOT / "templates" / "config.json", cfg)
    print(f"Initialised {hdir}. Edit {cfg} to list this project's gate commands.")
    return 0


def cmd_new(args) -> int:
    root = _root_or_die()
    tdir = H.task_dir(root, args.task_id)
    if tdir.exists():
        sys.exit(f"Task {args.task_id!r} already exists.")
    contract = json.loads((PLUGIN_ROOT / "templates" / "contract.json").read_text())
    contract.update({"id": args.task_id, "title": args.title, "rigor": args.rigor})
    H.write_json(tdir / "contract.json", contract)
    (tdir / "findings").mkdir(parents=True, exist_ok=True)
    H.save_state(root, args.task_id, {"phase": "contract", "round": 1, "stop_blocks": 0, "history": []})
    H.set_active_task(root, args.task_id)
    print(f"Created task {args.task_id} at {tdir}. Fill in contract.json, then run check-contract.")
    return 0


def cmd_status(args) -> int:
    root = _root_or_die()
    tid = H.active_task(root)
    if not tid:
        print("No active task.")
        return 0
    state = H.load_state(root, tid)
    contract = H.load_contract(root, tid) or {}
    print(json.dumps({"task": tid, "title": contract.get("title"), "rigor": contract.get("rigor"),
                      "phase": state["phase"], "round": state["round"]}, indent=2))
    return 0


def cmd_check_contract(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    rep = H.check_contract(H.load_contract(root, tid))
    print(rep.render())
    return 0 if rep.ok else 1


def cmd_phase(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    contract = H.load_contract(root, tid) or {}
    state = H.load_state(root, tid)
    target = args.phase
    if target in ("plan", "implement"):
        rep = H.check_contract(contract)
        if not rep.ok:
            print(rep.render())
            sys.exit(f"Cannot enter {target}: the contract does not pass check-contract.")
    if target == "implement" and contract.get("rigor") in ("standard", "full"):
        if not (H.task_dir(root, tid) / "plan.md").exists():
            sys.exit("Cannot enter implement: standard and full rigor need an approved plan.md.")
    if target == "verify":
        rep = readiness(root, tid)
        if not rep.ok:
            print(rep.render())
            sys.exit("Cannot enter verify: the task is not ready (see errors above).")
    if target == "implement" and state["phase"] == "verify":
        cfg = H.load_config(root)
        state["round"] += 1
        H.save_state(root, tid, state)
        if state["round"] > int(cfg["max_rounds"]):
            H.set_phase(root, tid, "escalated", f"exceeded {cfg['max_rounds']} rounds")
            sys.exit(f"Round limit ({cfg['max_rounds']}) reached. Task escalated to a human.")
    H.set_phase(root, tid, target, args.note or "")
    print(f"{tid}: phase -> {target}")
    return 0


def cmd_record(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    try:
        entry = H.record_evidence(root, tid, args.criterion, args.note or "")
    except H.HarnessError as exc:
        sys.exit(str(exc))
    status = "PASS" if entry.get("exit_code", 0) == 0 else f"FAIL (exit {entry['exit_code']})"
    print(f"{args.criterion}: {status}")
    if "output_excerpt" in entry:
        print(entry["output_excerpt"][-1500:])
    return 0 if entry.get("exit_code", 0) == 0 else 1


def cmd_check_ledger(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    rep = H.check_ledger(root, tid, rerun=args.rerun)
    print(rep.render())
    return 0 if rep.ok else 1


def cmd_gate(args) -> int:
    root = _root_or_die()
    ok, results = H.run_gates(root, only_on_edit=args.on_edit)
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['name']}  ({r['seconds']}s)")
        if not r["passed"]:
            print(r["output"])
    if not results:
        print("No gates configured in .harness/config.json.")
    return 0 if ok else 1


def cmd_ready(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    rep = readiness(root, tid)
    print(rep.render())
    return 0 if rep.ok else 1


def cmd_finish(args) -> int:
    root = _root_or_die()
    tid = _task_or_die(root, args.task)
    state = H.load_state(root, tid)
    if state["phase"] not in ("human", "verify") and not args.force:
        sys.exit(f"Task is in phase {state['phase']!r}; finish it from verify or human, or pass --force.")
    H.set_phase(root, tid, "done", args.note or "")
    H.set_active_task(root, None)
    print(f"{tid}: done")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="harness.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    s = sub.add_parser("new")
    s.add_argument("task_id")
    s.add_argument("--title", required=True)
    s.add_argument("--rigor", default="standard", choices=H.RIGOR_LEVELS)
    s.set_defaults(fn=cmd_new)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    for name, fn in (("check-contract", cmd_check_contract), ("ready", cmd_ready)):
        s = sub.add_parser(name)
        s.add_argument("--task")
        s.set_defaults(fn=fn)

    s = sub.add_parser("phase")
    s.add_argument("phase", choices=H.PHASES)
    s.add_argument("--note")
    s.add_argument("--task")
    s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("record")
    s.add_argument("criterion")
    s.add_argument("--note")
    s.add_argument("--task")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("check-ledger")
    s.add_argument("--rerun", action="store_true")
    s.add_argument("--task")
    s.set_defaults(fn=cmd_check_ledger)

    s = sub.add_parser("gate")
    s.add_argument("--on-edit", action="store_true")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("finish")
    s.add_argument("--note")
    s.add_argument("--force", action="store_true")
    s.add_argument("--task")
    s.set_defaults(fn=cmd_finish)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
