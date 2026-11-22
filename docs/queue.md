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
| 8 | #58 | plan+build |  |
| 9 | #20 | build | #6 |
| 10 | #45 | build |  |
| 11 | #8 | plan+build |  |
| 12 | #47 | plan+build | #8 |
| 13 | #9 | plan+build |  |
| 14 | #48 | plan+build |  |
| 15 | #16 | plan+build | #48 |
| 16 | #13 | issue |  |
| 17 | #14 | issue | #13 |
| 18 | #11 | issue |  |
| 19 | #12 | issue | #11 |
| 20 | #33 | issue | #6, #9, #13 |
| 21 | #24 | issue |  |
| 22 | #15 | plan+build |  |
| 23 | #29 | plan+build |  |
| 24 | #32 | plan+build | #29, #9, #15 |
| 25 | #21 | issue | #16 |
| 26 | #22 | issue | #16 |
| 27 | #23 | build |  |
| 28 | #51 | plan+build | #23 |
| 29 | #54 | plan+build | #23 |
| 30 | #55 | plan+build | #54 |
| 31 | #49 | issue |  |
| 32 | #17 | issue |  |
| 33 | #36 | issue |  |
| 34 | #37 | issue |  |
| 35 | #44 | issue |  |
| 36 | #52 | issue |  |
| 37 | #18 | plan+build | M1 |
| 38 | #30 | plan+build | #18 |
| 39 | #25 | plan+build | #18, #9, #15 |
| 40 | #31 | plan+build | #18 |
| 41 | #35 | plan+build | #18, #23 |
| 42 | #27 | plan+build | #18, #25 |
| 43 | #28 | plan+build | #27, #25 |
