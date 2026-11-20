# Work queue
Order and command for open issues. State (open/closed) comes from GitHub; closed rows
stay and are skipped. Every new issue gets a row here, placed by dependencies and
priority, in the same change that creates it. `/next` reads this file.
Commands: issue = /issue N · plan+build = /issue-plan N, approve, then /issue-build N ·
build = /issue-build N

| Order | Issue | Command | Depends on |
|---|---|---|---|
| 1 | #10 | issue | |
| 2 | #4 | issue | |
| 3 | #7 | build | |
| 4 | #5 | build | |
| 5 | #6 | plan+build | #5 |
| 6 | #20 | build | #6 |
| 7 | #8 | plan+build | |
| 8 | #9 | plan+build | |
| 9 | #13 | issue | |
| 10 | #14 | issue | #13 |
| 11 | #11 | issue | |
| 12 | #12 | issue | #11 |
| 13 | #24 | issue | |
| 14 | #15 | plan+build | |
| 15 | #16 | plan+build | |
| 16 | #21 | issue | #16 |
| 17 | #22 | issue | #16 |
| 18 | #17 | issue | |
| 19 | #23 | build | |
| 20 | #18 | plan+build | M1 |
| 21 | #25 | plan+build | #18, #9, #15 |
| 22 | #27 | plan+build | #18, #25 |
| 23 | #28 | plan+build | #27, #25 |
