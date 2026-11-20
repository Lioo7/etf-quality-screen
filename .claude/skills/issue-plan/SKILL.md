---
name: issue-plan
description: Plan a complex or spec-changing GitHub issue and post the plan on the issue for approval.
argument-hint: [issue-number]
disable-model-invocation: true
model: opus
effort: high
---
Plan GitHub issue #$0. Don't edit any files.

1. `gh issue view $0 --comments`. If a "Depends on" issue is still open, stop and tell me.
2. Read the code the issue touches.
3. Post the plan with `gh issue comment $0`, under ~60 lines: approach, files to change,
   tests mapped to "Done when", risks to the CLAUDE.md invariants, open questions.
4. Reply with the comment link and the open questions, then stop. I approve by running
   /issue-build $0.
Finally, read docs/queue.md and tell me which issue comes next once this PR is merged.
