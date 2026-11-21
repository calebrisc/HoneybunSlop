---
name: task-contract
description: Write and check a harness task contract (testable acceptance criteria with checks) for one unit of work. Use before any implementation in a project that has a .harness/ directory.
---

# Task contract

A contract says what done means in terms a machine can check. Everything later in the pipeline is judged against it, so it is worth getting right first.

The launcher is `~/.honeybun-slop/harness` (installed by the plugin's SessionStart hook). If the project has no `.harness/` yet, run `~/.honeybun-slop/harness init` at its root and fill `.harness/config.json` with the project's lint, build and test commands as gates (`on_edit: true` for the fast ones).

1. `~/.honeybun-slop/harness new <task-id> --title "<title>" --rigor <light|standard|full>`. Pick rigor by risk: `light` for small isolated changes, `standard` by default, `full` for save data, netcode, build or release scripts, or anything hard to undo.
2. Edit `.harness/tasks/<task-id>/contract.json`:
   - `source`: the request, or the `design/brief.md` and `design/requirements.md` sections (with requirement ids) this task implements.
   - `scope`: globs the implementer may change. `protected`: tests and fixtures the checks rely on.
   - `criteria`: one observable outcome each, with a `check`. Prefer `command` checks that fail today and will pass when done. `runtime` checks run the game headless (fixed seed, `--fixed-fps`, scripted input). `manual` only for things only a person can judge, like game feel. Add `fail_patterns` when a tool can print errors and still exit 0.
   - `out_of_scope`: what a reasonable person might expect but this task does not do.
3. Write any new tests the checks need now, before implementation, and commit them. They are protected from here on.
4. Run `~/.honeybun-slop/harness check-contract` until it prints OK.
5. Run the `contract-checker` agent and save its JSON to `.harness/tasks/<task-id>/findings/contract.json`. Fix every blocking finding and re-run it. Do not move on with blockers open.
