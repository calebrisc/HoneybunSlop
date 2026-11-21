"""Tests for the deterministic core. Run with: python3 -m pytest tests -q"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import harness_lib as H  # noqa: E402


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def cli(cwd, *args):
    return sh([sys.executable, str(SCRIPTS / "harness.py"), *args], cwd)


def hook(cwd, script, payload):
    payload = {"cwd": str(cwd), **payload}
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)], cwd=cwd, input=json.dumps(payload),
        capture_output=True, text=True,
    )


@pytest.fixture
def project(tmp_path):
    """A git repo with a tiny module, a test script and an initialised harness."""
    sh("git init -q -b main && git config user.email t@t && git config user.name t", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "score.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "check_add.py").write_text(
        "import sys; sys.path.insert(0, 'src')\nfrom score import add\nassert add(2, 3) == 5, add(2, 3)\nprint('ok')\n"
    )
    sh("git add -A && git commit -qm init", tmp_path)
    assert cli(tmp_path, "init").returncode == 0
    cfg = {"gates": [{"name": "compile", "cmd": f"{sys.executable} -c \"import ast; ast.parse(open('src/score.py').read())\"", "on_edit": True}],
           "max_stop_blocks": 2, "max_rounds": 2}
    (tmp_path / ".harness" / "config.json").write_text(json.dumps(cfg))
    assert cli(tmp_path, "new", "T1", "--title", "Fix add", "--rigor", "light").returncode == 0
    contract = H.load_contract(tmp_path, "T1")
    contract["protected"] = ["tests/**"]
    contract["criteria"] = [{
        "id": "AC1", "text": "add(2, 3) returns 5 when run by tests/check_add.py",
        "check": {"kind": "command", "command": f"{sys.executable} tests/check_add.py"},
    }]
    H.write_json(H.task_dir(tmp_path, "T1") / "contract.json", contract)
    return tmp_path


# ---------------------------------------------------------------- contract checks

def test_template_contract_is_rejected_until_filled(tmp_path):
    contract = json.loads((SCRIPTS.parent / "templates" / "contract.json").read_text())
    rep = H.check_contract(contract)
    assert not rep.ok


@pytest.mark.parametrize("text", [
    "The jump should work", "Movement feels good", "Menus are cleaner", "Load times are faster",
    "Handles input, sound, etc.",
])
def test_vague_criteria_are_rejected(text):
    rep = H.check_contract({"id": "x", "title": "x", "rigor": "light",
                            "criteria": [{"id": "A", "text": text, "check": {"kind": "command", "command": "true"}}]})
    assert any("vague" in e for e in rep.errors), rep.errors


def test_measurable_speed_criterion_is_allowed():
    rep = H.check_contract({"id": "x", "title": "x", "rigor": "light", "criteria": [
        {"id": "A", "text": "Level loads faster than 2.0 seconds on the CI runner", "check": {"kind": "command", "command": "true"}}]})
    assert rep.ok, rep.errors


def test_standard_rigor_needs_an_automated_check():
    rep = H.check_contract({"id": "x", "title": "x", "rigor": "standard", "criteria": [
        {"id": "A", "text": "Designer confirms the title screen layout", "check": {"kind": "manual"}}]})
    assert any("automated" in e for e in rep.errors)


def test_duplicate_ids_and_missing_commands():
    rep = H.check_contract({"id": "x", "title": "x", "rigor": "light", "criteria": [
        {"id": "A", "text": "Score is 10 after one coin", "check": {"kind": "command"}},
        {"id": "A", "text": "Score is 20 after two coins", "check": {"kind": "command", "command": "true"}}]})
    assert any("duplicate" in e for e in rep.errors)
    assert any("needs a command" in e for e in rep.errors)


# ---------------------------------------------------------------- phases

def test_cannot_skip_to_verify_without_evidence(project):
    assert cli(project, "phase", "implement").returncode == 0
    r = cli(project, "phase", "verify")
    assert r.returncode != 0
    assert "no evidence" in r.stdout


def test_standard_rigor_needs_plan_before_implement(project):
    c = H.load_contract(project, "T1"); c["rigor"] = "standard"
    H.write_json(H.task_dir(project, "T1") / "contract.json", c)
    assert cli(project, "phase", "implement").returncode != 0
    (H.task_dir(project, "T1") / "plan.md").write_text("1. fix the operator\n")
    assert cli(project, "phase", "implement").returncode == 0


# ---------------------------------------------------------------- ledger

def test_failing_evidence_is_recorded_as_failing(project):
    r = cli(project, "record", "AC1")
    assert r.returncode == 1 and "FAIL" in r.stdout
    assert any("exit code" in e for e in H.check_ledger(project, "T1").errors)


def test_passing_evidence_goes_stale_after_an_edit(project):
    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b\n")
    assert cli(project, "record", "AC1").returncode == 0
    assert H.check_ledger(project, "T1").ok
    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b  # tweak\n")
    rep = H.check_ledger(project, "T1")
    assert any("stale" in e for e in rep.errors)


def test_rerun_catches_evidence_that_no_longer_holds(project):
    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b\n")
    assert cli(project, "record", "AC1").returncode == 0
    # Simulate a forged ledger: fresh fingerprint, but the code is broken again.
    (project / "src" / "score.py").write_text("def add(a, b):\n    return (a * b) - 0\n")
    ledger = H.load_ledger(project, "T1")
    ledger["entries"][-1]["fingerprint"] = H.tree_fingerprint(project)
    H.write_json(H.ledger_path(project, "T1"), ledger)
    assert H.check_ledger(project, "T1").ok  # trusting the record alone is fooled
    rep = H.check_ledger(project, "T1", rerun=True)
    assert any("independent re-run" in e for e in rep.errors)


def test_evidence_for_a_changed_command_is_rejected(project):
    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b\n")
    assert cli(project, "record", "AC1").returncode == 0
    c = H.load_contract(project, "T1")
    c["criteria"][0]["check"]["command"] = "true"
    H.write_json(H.task_dir(project, "T1") / "contract.json", c)
    assert any("different command" in e for e in H.check_ledger(project, "T1").errors)


# ---------------------------------------------------------------- hooks

def test_stop_hook_blocks_then_allows_once_proven(project):
    assert cli(project, "phase", "implement").returncode == 0
    r = hook(project, "hook_stop_gate.py", {"hook_event_name": "Stop"})
    out = json.loads(r.stdout)
    assert out["decision"] == "block" and "AC1" in out["reason"]

    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b\n")
    assert cli(project, "record", "AC1").returncode == 0
    r = hook(project, "hook_stop_gate.py", {"hook_event_name": "Stop"})
    assert r.stdout.strip() == ""


def test_stop_hook_escalates_after_block_limit(project):
    assert cli(project, "phase", "implement").returncode == 0
    for _ in range(2):
        assert json.loads(hook(project, "hook_stop_gate.py", {}).stdout)["decision"] == "block"
    out = json.loads(hook(project, "hook_stop_gate.py", {}).stdout)
    assert "escalated" in out["systemMessage"]
    assert H.load_state(project, "T1")["phase"] == "escalated"


def test_stop_hook_ignores_verifier_subagents(project):
    assert cli(project, "phase", "implement").returncode == 0
    r = hook(project, "hook_stop_gate.py", {"hook_event_name": "SubagentStop", "agent_type": "correctness-verifier"})
    assert r.stdout.strip() == ""
    r = hook(project, "hook_stop_gate.py", {"hook_event_name": "SubagentStop", "agent_type": "honeybun-slop:implementer"})
    assert json.loads(r.stdout)["decision"] == "block"


def test_stop_hook_is_silent_outside_implement(project):
    assert hook(project, "hook_stop_gate.py", {}).stdout.strip() == ""


def test_protect_hook_denies_test_edits_during_implement(project):
    assert cli(project, "phase", "implement").returncode == 0
    r = hook(project, "hook_protect_paths.py", {"tool_name": "Edit", "tool_input": {"file_path": str(project / "tests" / "check_add.py")}})
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    r = hook(project, "hook_protect_paths.py", {"tool_name": "Edit", "tool_input": {"file_path": "src/score.py"}})
    assert r.stdout.strip() == ""


def test_protect_hook_denies_direct_ledger_writes(project):
    assert cli(project, "phase", "implement").returncode == 0
    r = hook(project, "hook_protect_paths.py", {"tool_name": "Bash", "tool_input": {"command": "echo '{}' > .harness/tasks/T1/ledger.json"}})
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    r = hook(project, "hook_protect_paths.py", {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/harness.py record AC1"}})
    assert r.stdout.strip() == ""
    r = hook(project, "hook_protect_paths.py", {"tool_name": "Bash", "tool_input": {"command": "cat .harness/tasks/T1/ledger.json"}})
    assert r.stdout.strip() == ""


def test_post_edit_hook_feeds_back_fast_gate_failures(project):
    assert cli(project, "phase", "implement").returncode == 0
    (project / "src" / "score.py").write_text("def add(a, b)\n    return a + b\n")
    out = json.loads(hook(project, "hook_post_edit.py", {"tool_name": "Edit"}).stdout)
    assert out["decision"] == "block" and "compile" in out["reason"]


def test_round_limit_escalates(project):
    (project / "src" / "score.py").write_text("def add(a, b):\n    return a + b\n")
    assert cli(project, "phase", "implement").returncode == 0
    assert cli(project, "record", "AC1").returncode == 0
    assert cli(project, "phase", "verify").returncode == 0
    assert cli(project, "phase", "implement").returncode == 0   # round 2
    assert cli(project, "phase", "verify").returncode == 0
    r = cli(project, "phase", "implement")                        # round 3 > max 2
    assert r.returncode != 0 and "escalated" in r.stderr


def test_exit_zero_with_error_output_counts_as_failure(project):
    c = H.load_contract(project, "T1")
    c["criteria"][0]["check"]["command"] = "echo 'SCRIPT ERROR: Invalid call. Nonexistent function' && exit 0"
    H.write_json(H.task_dir(project, "T1") / "contract.json", c)
    r = cli(project, "record", "AC1")
    assert r.returncode == 1 and "fail pattern" in r.stdout


def test_per_criterion_fail_patterns(project):
    c = H.load_contract(project, "T1")
    c["criteria"][0]["check"].update({"command": "echo 'frame time 41ms'", "fail_patterns": [r"frame time [4-9]\d+ms"]})
    H.write_json(H.task_dir(project, "T1") / "contract.json", c)
    assert cli(project, "record", "AC1").returncode == 1
