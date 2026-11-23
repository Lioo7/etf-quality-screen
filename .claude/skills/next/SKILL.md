---
name: next
description: Show the next backlog item and the exact command to run. Changes nothing.
disable-model-invocation: true
model: sonnet
effort: low
---
Don't edit anything.

1. `gh pr list --state open`. If an issue PR is open, reply "Merge or close PR #X first
   (send it for review)" and stop.
2. Read docs/queue.md. Walk the rows in order; skip closed issues
   (`gh issue view N --json state,title`). For the first open row, check its "Depends on"
   issues are closed; if not, note it as blocked and continue to the next row.
   Never output a row as Next if any issue in its "Depends on" is open; verify each with
   `gh issue view`.
3. List open issues that have no row in docs/queue.md
   (`gh issue list --state open --json number,title`) and warn about them.
4. Reply exactly:
   Next: #N <title>
   Run: /clear, then <command for that row>
   After that: #M <title> (<command>)
   Blocked: <rows skipped for open dependencies, if any>
