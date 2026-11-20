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
| 10 | #47 | plan+build | #8 |
| 11 | #9 | plan+build |  |
| 12 | #48 | plan+build |  |
| 13 | #16 | plan+build | #48 |
| 14 | #13 | issue |  |
| 15 | #14 | issue | #13 |
| 16 | #11 | issue |  |
| 17 | #12 | issue | #11 |
| 18 | #33 | issue | #6, #9, #13 |
| 19 | #24 | issue |  |
| 20 | #15 | plan+build |  |
| 21 | #29 | plan+build |  |
| 22 | #32 | plan+build | #29, #9, #15 |
| 23 | #21 | issue | #16 |
| 24 | #22 | issue | #16 |
| 25 | #49 | issue |  |
| 26 | #17 | issue |  |
| 27 | #36 | issue |  |
| 28 | #37 | issue |  |
| 29 | #44 | issue |  |
| 30 | #23 | build |  |
| 31 | #18 | plan+build | M1 |
| 32 | #30 | plan+build | #18 |
| 33 | #25 | plan+build | #18, #9, #15 |
| 34 | #31 | plan+build | #18 |
| 35 | #35 | plan+build | #18, #23 |
| 36 | #27 | plan+build | #18, #25 |
| 37 | #28 | plan+build | #27, #25 |
