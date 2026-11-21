---
name: ship
description: Run the full verifier/prover pipeline on the active harness task, from plan check through implementation, independent verification, refutation and the human gate. Use after /task-contract has produced a passing contract.
---

# Ship a task

Run each step in order. The launcher is `~/.honeybun-slop/harness`. Spawn the named agents from this plugin; never use a forked subagent for a verifier or refuter, because a fork inherits this conversation and stops being independent.

Read rigor from the contract. Steps marked (standard+) are skipped at `light`; steps marked (full) run only at `full`.

1. **Plan (standard+).** Write `.harness/tasks/<id>/plan.md`: numbered steps, files touched, how each criterion will be met. Run `harness phase plan`, then the `plan-verifier` agent, saving its JSON to `findings/plan.json`. Fix blockers and re-run it.
2. **Implement.** `harness phase implement`, then run the `implementer` agent with only the task id as its prompt. Do not pass it your own summary of the task; it reads the contract. The Stop hook keeps it working until `harness ready` passes or it escalates.
3. **Gate.** Run `harness phase verify`. It refuses unless the contract, the ledger and all gates pass. If it refuses, go back to step 2.
4. **Verify.** In parallel, run `correctness-verifier`, and (standard+) `spec-verifier`. Give each only the task id. Never show one verifier another's output, and never show them the implementer's report. Save each JSON to `findings/round-<n>-<verifier>.json`.
5. **Refute (standard+).** For each blocking finding, run one `refuter` with that finding alone. Save results to `findings/round-<n>-refuted.json`. At `light`, every blocking finding counts as confirmed.
6. **Decide.**
   - Confirmed blockers: write them to `findings/round-<n>-confirmed.json`, run `harness phase implement` (this counts a round; past `max_rounds` the task escalates), and go to step 2. The implementer reads the confirmed file.
   - No confirmed blockers: run `harness phase human` at `full`, or when any criterion is manual or any finding is `unproven`. Otherwise finish with `harness finish`.
7. **Human gate.** Show the person: the contract title, one line per criterion with its ledger status, manual criteria with what to look at, unproven findings and notes. Only the person approves. Then `harness finish --note "approved by <name>"`.

When a task escalates, stop and show the person the unresolved findings and the last gate output. Do not keep looping.
