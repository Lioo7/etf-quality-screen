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
| 6 | #46 | build | #6 |
| 7 | #20 | build | #6 |
| 8 | #45 | build |  |
| 9 | #8 | plan+build |  |
| 10 | #9 | plan+build |  |
| 11 | #16 | plan+build |  |
| 12 | #13 | issue |  |
| 13 | #14 | issue | #13 |
| 14 | #11 | issue |  |
| 15 | #12 | issue | #11 |
| 16 | #33 | issue | #6, #9, #13 |
| 17 | #24 | issue |  |
| 18 | #15 | plan+build |  |
| 19 | #29 | plan+build |  |
| 20 | #32 | plan+build | #29, #9, #15 |
| 21 | #21 | issue | #16 |
| 22 | #22 | issue | #16 |
| 23 | #17 | issue |  |
| 24 | #36 | issue |  |
| 25 | #37 | issue |  |
| 26 | #44 | issue |  |
| 27 | #23 | build |  |
| 28 | #18 | plan+build | M1 |
| 29 | #30 | plan+build | #18 |
| 30 | #25 | plan+build | #18, #9, #15 |
| 31 | #31 | plan+build | #18 |
| 32 | #35 | plan+build | #18, #23 |
| 33 | #27 | plan+build | #18, #25 |
| 34 | #28 | plan+build | #27, #25 |
