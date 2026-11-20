# Work queue
Order and command for open issues. State (open/closed) comes from GitHub; closed rows
stay and are skipped. Every new issue gets a row here, placed by dependencies and
priority, in the same change that creates it. `/next` reads this file.
Commands: issue = /issue N · plan+build = /issue-plan N, approve, then /issue-build N ·
build = /issue-build N

| Order | Issue | Command | Depends on |
|---|---|---|---|
| 1 | #10 | issue |  |
| 2 | #4 | issue |  |
| 3 | #7 | build |  |
| 4 | #5 | build |  |
| 5 | #6 | plan+build | #5 |
| 6 | #20 | build | #6 |
| 7 | #8 | plan+build |  |
| 8 | #9 | plan+build |  |
| 9 | #16 | plan+build |  |
| 10 | #13 | issue |  |
| 11 | #14 | issue | #13 |
| 12 | #11 | issue |  |
| 13 | #12 | issue | #11 |
| 14 | #33 | issue | #6, #9, #13 |
| 15 | #24 | issue |  |
| 16 | #15 | plan+build |  |
| 17 | #29 | plan+build |  |
| 18 | #32 | plan+build | #29, #9, #15 |
| 19 | #21 | issue | #16 |
| 20 | #22 | issue | #16 |
| 21 | #17 | issue |  |
| 22 | #36 | issue |  |
| 23 | #37 | issue |  |
| 24 | #23 | build |  |
| 25 | #18 | plan+build | M1 |
| 26 | #30 | plan+build | #18 |
| 27 | #25 | plan+build | #18, #9, #15 |
| 28 | #31 | plan+build | #18 |
| 29 | #35 | plan+build | #18, #23 |
| 30 | #27 | plan+build | #18, #25 |
| 31 | #28 | plan+build | #27, #25 |
