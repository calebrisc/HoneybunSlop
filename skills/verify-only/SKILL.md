---
name: verify-only
description: Run the independent verifiers and refuter on the active harness task without implementing anything. Use to audit work done outside the pipeline or to re-check a task before merging.
---

# Verify only

1. `~/.honeybun-slop/harness check-ledger --rerun` and `~/.honeybun-slop/harness gate`. Report failures as they are.
2. Run `correctness-verifier` and `spec-verifier` in parallel, each given only the task id. Never use forked subagents for this.
3. Run one `refuter` per blocking finding.
4. Report: confirmed findings first with their evidence, then unproven ones, then notes. Do not change any code. If there is no active task, ask which task to verify, or offer to write a contract for the change with /task-contract.
