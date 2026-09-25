---
name: decision-log
description: >-
  Appends a concise entry to the repo-root decision.md whenever an
  architecture or product choice changes. Use on any implementation,
  fix, refactor, or design task in a repo that contains decision.md,
  and when the user mentions the decision log, decision.md, or asks
  to record a decision. Project choices only.
---

# Decision log

ACTIVE EVERY RESPONSE while `decision.md` exists at the repo root. No drift after many turns. Still active if unsure. Off only: "stop decision log" / "normal mode".

Ponytail does not skip this file. A major choice with no log line is unfinished. Write the entry in the same turn as the code.

## When to write

Write when the choice changes architecture or product behavior: data store, model split, id scheme, deploy target, auth, notification channel, a phase gate the user set.

Skip: same-behavior refactors, bugfixes that restore an existing decision, formatting, dependency bumps, and entries about this log or ponytail.

## How

1. Read `decision.md`. Next id is the next `D-00N`.
2. New choice → append one entry. Leave older **Decision** / **Why** / **Advantage** / **Expected** lines alone.
3. Same choice, outcome now known → update that entry's **Actual** only.
4. Five lines. No essays.

## Format

```markdown
## D-00N — <short title>
- **Decision:**
- **Why:**
- **Advantage:**
- **Expected:**
- **Actual:** pending
```

Fill **Actual** once the outcome is known. Until then write `pending`.
