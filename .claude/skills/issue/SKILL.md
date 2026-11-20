---
name: issue
description: Implement one routine GitHub issue end to end, up to an open PR.
argument-hint: [issue-number]
disable-model-invocation: true
model: sonnet
effort: medium
---
Implement GitHub issue #$0.

1. `gh issue view $0 --comments`. If a "Depends on" issue is still open, stop and tell me.
2. If the scope is unclear or the work needs anything outside "Scope: In", stop and ask.
   Never edit the issue's scope yourself.
3. `git switch main && git pull`, then create branch `issue-$0-<short-slug>`.
4. Write the tests for "Done when" first and watch them fail; then implement.
5. If .claude/agents/reviewer.md exists, run the reviewer on the diff against the issue and
   fix the correctness gaps it reports.
6. Produce the evidence "Done when" asks for (commands and their output).
7. Update README/CHANGELOG/docs only where the issue asks; a spec-change issue always adds a
   docs/decisions.md entry.
8. Commit (Conventional Commits; body ends with `closes #$0`), push the branch, open a PR
   whose body contains the evidence. Never merge.
9. Reply with: the PR link, 3–5 bullets on what changed, the evidence, anything left open.
Finally, read docs/queue.md and tell me which issue comes next once this PR is merged.
