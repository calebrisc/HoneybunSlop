---
name: game-intake
description: Turn a written game description and hand-drawn images into a verified game design brief, engine requirements and an engine recommendation. Use when starting a new game or a major feature, before writing task contracts.
---

# Game intake

The person describes the game; you produce a brief a team can build from, and an independent verifier checks it is faithful to what they said and drew.

1. **Collect.** Make sure everything is under `design/intake/`: text or markdown files and image files. If the person pasted text or attached images in chat, save them there first (one file per message or image, named in order, e.g. `01-pitch.md`, `02-sketch-level.png`). Do not paraphrase what they wrote; save it verbatim.
2. **Draft.** Run the `design-intake` agent. It writes `design/brief.md` and `design/requirements.md`.
3. **Verify.** Run the `intake-verifier` agent. It sees only the files, not this conversation. Save its JSON to `design/intake-findings.json`.
4. **Confirm blockers.** For each blocking finding, run the `refuter` agent with that one finding. Drop findings it refutes; keep `unproven` ones as notes for the person.
5. **Revise.** If confirmed blockers remain, run `design-intake` again (it reads the findings file) and repeat steps 3 and 4. Stop after 2 revision rounds and show the person what is still unresolved.
6. **Hand to the person.** Show the pitch paragraph, the engine recommendation with its two deciding requirements, and the open questions, numbered. The person's answers go into `design/intake/` as a new file, and you run the loop again. Nothing gets a task contract until the person approves the brief; record approval by adding `Approved by <name> on <date>` at the top of `design/brief.md`.

Never fill a gap in the person's design with your own idea. Gaps become open questions.
