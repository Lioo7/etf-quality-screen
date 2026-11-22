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
| 7 | #53 | issue |  |
| 8 | #20 | build | #6 |
| 9 | #45 | build |  |
| 10 | #8 | plan+build |  |
| 11 | #47 | plan+build | #8 |
| 12 | #9 | plan+build |  |
| 13 | #48 | plan+build |  |
| 14 | #16 | plan+build | #48 |
| 15 | #13 | issue |  |
| 16 | #14 | issue | #13 |
| 17 | #11 | issue |  |
| 18 | #12 | issue | #11 |
| 19 | #33 | issue | #6, #9, #13 |
| 20 | #24 | issue |  |
| 21 | #15 | plan+build |  |
| 22 | #29 | plan+build |  |
| 23 | #32 | plan+build | #29, #9, #15 |
| 24 | #21 | issue | #16 |
| 25 | #22 | issue | #16 |
| 26 | #23 | build |  |
| 27 | #51 | plan+build | #23 |
| 28 | #54 | plan+build | #23 |
| 29 | #55 | plan+build | #54 |
| 30 | #49 | issue |  |
| 31 | #17 | issue |  |
| 32 | #36 | issue |  |
| 33 | #37 | issue |  |
| 34 | #44 | issue |  |
| 35 | #52 | issue |  |
| 36 | #18 | plan+build | M1 |
| 37 | #30 | plan+build | #18 |
| 38 | #25 | plan+build | #18, #9, #15 |
| 39 | #31 | plan+build | #18 |
| 40 | #35 | plan+build | #18, #23 |
| 41 | #27 | plan+build | #18, #25 |
| 42 | #28 | plan+build | #27, #25 |
