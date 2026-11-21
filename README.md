# Honeybun Slop

A Claude Code plugin that runs game development work through layers of provers and verifiers, so nothing ships on an agent's word alone.

The design and the research behind it: [Verifier/prover harness: research and proposed design](https://claude.ai/artifact/5h8x9oqV9C1KdnCv7YraNi).

## How it works

| Layer | What runs | Blocks? |
| --- | --- | --- |
| Design intake | `design-intake` turns your written description and drawings into a brief and engine requirements; `intake-verifier` checks every requirement traces to something you wrote or drew | Yes, until you approve the brief |
| Task contract | `harness check-contract` (structure, vague wording, placeholders) and the `contract-checker` agent (coverage, checks that really measure the criterion) | Yes |
| Plan check | `plan-verifier` (standard and full rigor) | Yes |
| Implementer | `implementer` agent; evidence is recorded by `harness record`, which runs the check itself | No |
| Deterministic gates | Hooks: fast gates after each edit, path protection for tests and harness state, and a Stop gate that refuses to let the implementer finish until every criterion has fresh, passing evidence | Always |
| Independent verifiers | `correctness-verifier` (re-runs everything, hunts bugs and test gaming) and `spec-verifier` (fit to intent and scope), read-only, never shown the implementer's report | Only through the refuter |
| Refuter | `refuter` tries to disprove each blocking finding before it costs a rework round | Decides |
| Human gate | You, for game feel, manual criteria and anything irreversible | Yes |

Evidence is tied to a fingerprint of the working tree, so any edit after a passing run makes it stale. Output containing `SCRIPT ERROR`, a Python traceback and similar counts as failure even when the process exits 0. Rework is capped at 3 rounds and 3 blocked stops per task, after which the task escalates to a person.

## Use it

1. Install the plugin from this repository (for example `claude plugin install` from a marketplace that lists it, or `claude --plugin-dir /path/to/HoneybunSlop` while developing).
2. In a game project: start a session once so the SessionStart hook installs the `~/.honeybun-slop/harness` launcher, then run `~/.honeybun-slop/harness init` and list your lint, build and test commands as gates in `.harness/config.json`.
3. `/game-intake` for a new game or major feature, `/task-contract` for each unit of work, `/ship` to run it through the pipeline, `/verify-only` to audit existing work.

Set `HARNESS_DISABLE=1` to turn the hooks off for a session.

## Commands

```
harness init | new <id> --title ... [--rigor light|standard|full] | status
harness check-contract | phase <phase> | record <criterion> [--note ...]
harness check-ledger [--rerun] | gate [--on-edit] | ready | finish
```

## Develop

```
python3 -m pytest tests -q
claude plugin validate .
```

## Roadmap

1. Deterministic core: contracts, evidence ledger, gates, hooks. **Done.**
2. Intake, verifier and refuter agents and the pipeline skills. **First version done.**
3. Game runtime layer per engine, chosen after intake: engine MCP, headless playtests with fixed seeds and frame rate, screenshots and video as evidence, a runtime verifier.
4. Seeded-bug eval set with per-layer catch and false-positive rates, run through `claude plugin eval`.
