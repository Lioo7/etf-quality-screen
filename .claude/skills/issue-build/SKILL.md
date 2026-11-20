---
name: issue-build
description: Implement a complex or research GitHub issue, following its approved plan comment if one exists.
argument-hint: [issue-number]
disable-model-invocation: true
model: opus
effort: high
---
Implement GitHub issue #$0 using steps 1–9 of .claude/skills/issue/SKILL.md, with two
additions:
- If the issue has a plan comment from /issue-plan, follow it. If you need to deviate,
  stop and explain why before changing course.
- For a research issue, the deliverable is the findings doc plus a summary comment on the
  issue; don't change etf_screen/ or tests/.
Finally, read docs/queue.md and tell me which issue comes next once this PR is merged.
