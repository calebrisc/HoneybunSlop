# Honeybun Slop (plugin source)

- This repo is the plugin itself: `agents/`, `skills/`, `hooks/hooks.json`, `scripts/` (stdlib-only Python), `templates/`, `schemas/`.
- Every behaviour in `scripts/` needs a test in `tests/test_harness.py`. Run `python3 -m pytest tests -q` and `claude plugin validate .` before committing.
- Hooks must stay deterministic and fast, and must do nothing outside a project with `.harness/` or outside the implement phase.
- Agent prompts: verifiers are read-only and never receive the implementer's report or each other's output. Keep it that way.
