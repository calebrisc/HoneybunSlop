"""Shared logic for the Honeybun Slop harness.

Everything here is deterministic and uses only the Python standard library, so the
same checks run inside Claude Code hooks, from the command line and in CI.

State lives in the project being worked on, under `.harness/`:

    .harness/config.json            gate commands and limits (see templates/config.json)
    .harness/active                 id of the task currently in progress
    .harness/tasks/<id>/contract.json
    .harness/tasks/<id>/plan.md
    .harness/tasks/<id>/ledger.json written only by `harness.py record`
    .harness/tasks/<id>/state.json  phase, round and stop-block counters
    .harness/tasks/<id>/findings/   verifier and refuter output
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = ".harness"
PHASES = ("intake", "contract", "plan", "implement", "verify", "human", "done", "escalated")
RIGOR_LEVELS = ("light", "standard", "full")
CHECK_KINDS = ("command", "runtime", "manual")
OUTPUT_EXCERPT_CHARS = 4000

# Words that make an acceptance criterion untestable. The contract checker agent does
# the semantic review; this list only catches the obvious cases cheaply.
VAGUE_PATTERNS = [
    r"\bshould work\b",
    r"\bworks? (well|correctly|properly|fine)\b",
    r"\bproperly\b",
    r"\bnicely?\b",
    r"\bbetter\b",
    r"\bclean(er)?\b",
    r"\bfast(er)?\b(?!.*\d)",
    r"\bintuitive\b",
    r"\bas (needed|appropriate)\b",
    r"\betc\.?\b",
    r"\band so on\b",
    r"\bfeels? (good|right)\b",
]

# Build and cache output that checks commonly create. Changes here never make evidence
# stale. Projects add their own with `fingerprint_exclude` in config.json, and anything
# already in .gitignore is skipped automatically.
DEFAULT_FINGERPRINT_EXCLUDE = [
    "**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", "**/node_modules/**",
    ".godot/**", "**/.import/**", "Library/**", "Temp/**", "Logs/**", "obj/**",
    "Intermediate/**", "Saved/**", "DerivedDataCache/**", "build/**", "dist/**",
]

# Output that means failure even when the process exits 0. Godot, for one, can print
# SCRIPT ERROR and still exit 0, so exit codes alone are not evidence.
DEFAULT_FAIL_PATTERNS = [
    r"SCRIPT ERROR",
    r"Traceback \(most recent call last\)",
    r"^\s*FATAL\b",
    r"\bSegmentation fault\b",
]

DEFAULT_CONFIG = {
    "gates": [],
    "fail_patterns": DEFAULT_FAIL_PATTERNS,
    "fingerprint_exclude": [],
    "max_stop_blocks": 3,
    "max_rounds": 3,
    "gate_timeout_seconds": 600,
}


class HarnessError(Exception):
    pass


# ---------------------------------------------------------------- paths and state


def find_root(start: str | os.PathLike | None = None) -> Path | None:
    """Return the nearest directory at or above `start` that holds `.harness/`."""
    here = Path(start or os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / HARNESS_DIR).is_dir():
            return candidate
    return None


def harness_dir(root: Path) -> Path:
    return root / HARNESS_DIR


def task_dir(root: Path, task_id: str) -> Path:
    return harness_dir(root) / "tasks" / task_id


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


def load_config(root: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(harness_dir(root) / "config.json", {}) or {})
    return cfg


def active_task(root: Path) -> str | None:
    path = harness_dir(root) / "active"
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def set_active_task(root: Path, task_id: str | None) -> None:
    path = harness_dir(root) / "active"
    if task_id is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(task_id + "\n", encoding="utf-8")


def load_state(root: Path, task_id: str) -> dict:
    return load_json(task_dir(root, task_id) / "state.json", None) or {
        "phase": "contract",
        "round": 1,
        "stop_blocks": 0,
        "history": [],
    }


def save_state(root: Path, task_id: str, state: dict) -> None:
    write_json(task_dir(root, task_id) / "state.json", state)


def set_phase(root: Path, task_id: str, phase: str, note: str = "") -> dict:
    if phase not in PHASES:
        raise HarnessError(f"unknown phase {phase!r}; expected one of {', '.join(PHASES)}")
    state = load_state(root, task_id)
    state["history"].append({"at": _now(), "from": state["phase"], "to": phase, "note": note})
    state["phase"] = phase
    if phase == "implement":
        state["stop_blocks"] = 0
    save_state(root, task_id, state)
    return state


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- contract


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"ERROR: {e}" for e in self.errors] + [f"WARN: {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "OK"


def check_contract(contract: dict | None) -> Report:
    """Structural and lexical checks on a task contract."""
    rep = Report()
    if not contract:
        rep.errors.append("contract.json is missing or empty")
        return rep
    for key in ("id", "title", "rigor", "criteria"):
        if key not in contract:
            rep.errors.append(f"contract is missing '{key}'")
    if contract.get("rigor") not in RIGOR_LEVELS:
        rep.errors.append(f"rigor must be one of {', '.join(RIGOR_LEVELS)}")
    criteria = contract.get("criteria") or []
    if not criteria:
        rep.errors.append("contract has no acceptance criteria")
    seen = set()
    for i, crit in enumerate(criteria):
        where = f"criterion {crit.get('id', i)}"
        cid = crit.get("id")
        if not cid:
            rep.errors.append(f"{where}: missing id")
        elif cid in seen:
            rep.errors.append(f"{where}: duplicate id")
        seen.add(cid)
        text = (crit.get("text") or "").strip()
        if "TODO" in text or "TODO" in str((crit.get("check") or {}).get("command", "")):
            rep.errors.append(f"{where}: still has TODO placeholders from the template")
        if len(text) < 10:
            rep.errors.append(f"{where}: text is missing or too short to test")
        for pat in VAGUE_PATTERNS:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                rep.errors.append(
                    f"{where}: vague wording '{m.group(0)}'; say what is observed and how it is measured"
                )
        check = crit.get("check") or {}
        kind = check.get("kind")
        if kind not in CHECK_KINDS:
            rep.errors.append(f"{where}: check.kind must be one of {', '.join(CHECK_KINDS)}")
        elif kind in ("command", "runtime") and not (check.get("command") or "").strip():
            rep.errors.append(f"{where}: a {kind} check needs a command")
        elif kind == "manual":
            rep.warnings.append(f"{where}: manual check, a human must confirm it at the human gate")
    for key in ("scope", "protected"):
        val = contract.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            rep.errors.append(f"'{key}' must be a list of glob strings")
    if contract.get("rigor") in ("standard", "full") and all(
        (c.get("check") or {}).get("kind") == "manual" for c in criteria
    ):
        rep.errors.append("standard and full rigor need at least one automated (command or runtime) check")
    return rep


def load_contract(root: Path, task_id: str) -> dict | None:
    return load_json(task_dir(root, task_id) / "contract.json", None)


# ---------------------------------------------------------------- tree fingerprint


def _git(root: Path, *args: str, binary: bool = False):
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=not binary, check=False
    )
    if proc.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {proc.stderr!r}")
    return proc.stdout


def tree_fingerprint(root: Path) -> str:
    """Hash of the working tree content, excluding `.harness/`.

    Evidence recorded against one fingerprint goes stale as soon as any tracked or
    untracked (non-ignored) file changes, so a passing test run cannot be reused after
    further edits.
    """
    h = hashlib.sha256()
    cfg = load_config(root)
    excludes = [f":(exclude){HARNESS_DIR}"] + [
        f":(exclude,glob){g}" for g in DEFAULT_FINGERPRINT_EXCLUDE + list(cfg.get("fingerprint_exclude", []))
    ]
    try:
        head = _git(root, "rev-parse", "--verify", "-q", "HEAD").strip()
    except HarnessError:
        head = "no-head"
    h.update(head.encode())
    if head != "no-head":
        h.update(_git(root, "diff", "HEAD", "--binary", "--", ".", *excludes, binary=True))
    untracked = _git(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".", *excludes
    ).split("\0")
    if head == "no-head":
        untracked += _git(root, "ls-files", "-z", "--", ".", *excludes).split("\0")
    for rel in sorted(set(p for p in untracked if p)):
        p = root / rel
        h.update(rel.encode() + b"\0")
        if p.is_file():
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


# ---------------------------------------------------------------- running checks


@dataclass
class RunResult:
    command: str
    exit_code: int
    output: str
    seconds: float
    matched_fail_pattern: str | None = None


def scan_fail_patterns(output: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        if re.search(pat, output, flags=re.MULTILINE):
            return pat
    return None


def run_command(root: Path, command: str, timeout: int = 600, fail_patterns: list[str] | None = None) -> RunResult:
    """Run a check. A zero exit whose output matches a fail pattern counts as exit 1."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "HARNESS_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        out = f"timed out after {timeout}s\n{partial}"
    if fail_patterns is None:
        fail_patterns = load_config(root).get("fail_patterns", DEFAULT_FAIL_PATTERNS)
    matched = scan_fail_patterns(out, fail_patterns) if code == 0 else None
    if matched:
        code = 1
        out += f"\n[harness] exit code 0 overridden: output matched fail pattern {matched!r}"
    return RunResult(command, code, out, round(time.monotonic() - start, 2), matched)


def excerpt(text: str, limit: int = OUTPUT_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text) - limit} chars cut] ...\n" + text[-half:]


def run_gates(root: Path, only_on_edit: bool = False) -> tuple[bool, list[dict]]:
    cfg = load_config(root)
    results = []
    ok = True
    for gate in cfg.get("gates", []):
        if only_on_edit and not gate.get("on_edit", False):
            continue
        res = run_command(root, gate["cmd"], timeout=int(gate.get("timeout", cfg["gate_timeout_seconds"])))
        passed = res.exit_code == 0
        ok = ok and passed
        results.append(
            {
                "name": gate.get("name", gate["cmd"]),
                "cmd": gate["cmd"],
                "passed": passed,
                "exit_code": res.exit_code,
                "seconds": res.seconds,
                "output": excerpt(res.output, 1500),
            }
        )
    return ok, results


# ---------------------------------------------------------------- ledger


def ledger_path(root: Path, task_id: str) -> Path:
    return task_dir(root, task_id) / "ledger.json"


def load_ledger(root: Path, task_id: str) -> dict:
    return load_json(ledger_path(root, task_id), None) or {"task_id": task_id, "entries": []}


def record_evidence(root: Path, task_id: str, criterion_id: str, note: str = "") -> dict:
    """Run the contract's check for one criterion and append the result to the ledger.

    The harness runs the command itself and stores its real exit code, output hash and
    the tree fingerprint. The agent never types evidence by hand.
    """
    contract = load_contract(root, task_id)
    if not contract:
        raise HarnessError("no contract for the active task")
    crit = next((c for c in contract["criteria"] if c.get("id") == criterion_id), None)
    if crit is None:
        raise HarnessError(f"criterion {criterion_id!r} is not in the contract")
    check = crit.get("check") or {}
    entry = {
        "criterion": criterion_id,
        "kind": check.get("kind"),
        "recorded_at": _now(),
        # Taken before the check runs, so it names the exact tree the check tested.
        "fingerprint": tree_fingerprint(root),
    }
    if check.get("kind") in ("command", "runtime"):
        cfg = load_config(root)
        patterns = list(cfg.get("fail_patterns", DEFAULT_FAIL_PATTERNS)) + list(check.get("fail_patterns", []))
        res = run_command(root, check["command"], timeout=int(check.get("timeout", cfg["gate_timeout_seconds"])),
                          fail_patterns=patterns)
        entry.update(
            {
                "command": res.command,
                "exit_code": res.exit_code,
                "seconds": res.seconds,
                "output_sha256": hashlib.sha256(res.output.encode()).hexdigest(),
                "output_excerpt": excerpt(res.output),
            }
        )
    else:
        if not note.strip():
            raise HarnessError("a manual criterion needs --note describing what was done and how to check it")
        entry["note"] = note.strip()
    ledger = load_ledger(root, task_id)
    ledger["entries"].append(entry)
    write_json(ledger_path(root, task_id), ledger)
    return entry


def check_ledger(root: Path, task_id: str, rerun: bool = False) -> Report:
    """Every criterion needs a passing entry recorded against the current tree.

    With rerun=True each command is executed again, which is what an independent
    verifier does instead of trusting the recorded result.
    """
    rep = Report()
    contract = load_contract(root, task_id)
    if not contract:
        rep.errors.append("no contract for the active task")
        return rep
    ledger = load_ledger(root, task_id)
    fp = tree_fingerprint(root)
    latest: dict[str, dict] = {}
    for entry in ledger.get("entries", []):
        latest[entry.get("criterion")] = entry
    for crit in contract.get("criteria", []):
        cid = crit.get("id")
        kind = (crit.get("check") or {}).get("kind")
        entry = latest.get(cid)
        if entry is None:
            rep.errors.append(f"{cid}: no evidence recorded (run: harness.py record {cid})")
            continue
        if kind == "manual":
            rep.warnings.append(f"{cid}: manual evidence, needs human confirmation: {entry.get('note', '')[:120]}")
            continue
        if entry.get("command") != (crit.get("check") or {}).get("command"):
            rep.errors.append(f"{cid}: evidence was recorded for a different command than the contract's")
            continue
        if entry.get("fingerprint") != fp:
            rep.errors.append(f"{cid}: evidence is stale, files changed after it was recorded (re-record it)")
            continue
        if entry.get("exit_code") != 0:
            rep.errors.append(f"{cid}: check failed with exit code {entry.get('exit_code')}")
            continue
        if rerun:
            cfg = load_config(root)
            patterns = list(cfg.get("fail_patterns", DEFAULT_FAIL_PATTERNS)) + list(
                (crit.get("check") or {}).get("fail_patterns", []))
            res = run_command(root, entry["command"], fail_patterns=patterns)
            if res.exit_code != 0:
                rep.errors.append(
                    f"{cid}: recorded as passing but failed on independent re-run (exit {res.exit_code}):\n"
                    + excerpt(res.output, 800)
                )
    return rep


# ---------------------------------------------------------------- path protection


def matches_any(rel_path: str, globs: list[str]) -> bool:
    rel_path = rel_path.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel_path, g) or fnmatch.fnmatch(rel_path, g.rstrip("/") + "/*") for g in globs)


def protected_globs(root: Path, task_id: str) -> list[str]:
    contract = load_contract(root, task_id) or {}
    return list(contract.get("protected", [])) + [
        f"{HARNESS_DIR}/tasks/*/contract.json",
        f"{HARNESS_DIR}/tasks/*/ledger.json",
        f"{HARNESS_DIR}/tasks/*/state.json",
        f"{HARNESS_DIR}/config.json",
        f"{HARNESS_DIR}/active",
    ]
