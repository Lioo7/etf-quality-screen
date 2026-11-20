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
| 7 | #45 | build |  |
| 8 | #8 | plan+build |  |
| 9 | #9 | plan+build |  |
| 10 | #16 | plan+build |  |
| 11 | #13 | issue |  |
| 12 | #14 | issue | #13 |
| 13 | #11 | issue |  |
| 14 | #12 | issue | #11 |
| 15 | #33 | issue | #6, #9, #13 |
| 16 | #24 | issue |  |
| 17 | #15 | plan+build |  |
| 18 | #29 | plan+build |  |
| 19 | #32 | plan+build | #29, #9, #15 |
| 20 | #21 | issue | #16 |
| 21 | #22 | issue | #16 |
| 22 | #17 | issue |  |
| 23 | #36 | issue |  |
| 24 | #37 | issue |  |
| 25 | #44 | issue |  |
| 26 | #23 | build |  |
| 27 | #18 | plan+build | M1 |
| 28 | #30 | plan+build | #18 |
| 29 | #25 | plan+build | #18, #9, #15 |
| 30 | #31 | plan+build | #18 |
| 31 | #35 | plan+build | #18, #23 |
| 32 | #27 | plan+build | #18, #25 |
| 33 | #28 | plan+build | #27, #25 |
