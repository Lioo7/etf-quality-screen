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
| 9 | #61 | plan+build | #53 |
| 10 | #20 | build | #6 |
| 11 | #45 | build |  |
| 12 | #8 | plan+build |  |
| 13 | #47 | plan+build | #8 |
| 14 | #9 | plan+build |  |
| 15 | #48 | plan+build |  |
| 16 | #16 | plan+build | #48 |
| 17 | #13 | issue |  |
| 18 | #14 | issue | #13 |
| 19 | #11 | issue |  |
| 20 | #12 | issue | #11 |
| 21 | #33 | issue | #6, #9, #13 |
| 22 | #24 | issue |  |
| 23 | #15 | plan+build |  |
| 24 | #29 | plan+build |  |
| 25 | #32 | plan+build | #29, #9, #15 |
| 26 | #21 | issue | #16 |
| 27 | #22 | issue | #16 |
| 28 | #23 | build |  |
| 29 | #51 | plan+build | #23 |
| 30 | #54 | plan+build | #23 |
| 31 | #55 | plan+build | #54 |
| 32 | #49 | issue |  |
| 33 | #17 | issue |  |
| 34 | #36 | issue |  |
| 35 | #37 | issue |  |
| 36 | #44 | issue |  |
| 37 | #52 | issue |  |
| 38 | #18 | plan+build | M1 |
| 39 | #30 | plan+build | #18 |
| 40 | #25 | plan+build | #18, #9, #15 |
| 41 | #31 | plan+build | #18 |
| 42 | #35 | plan+build | #18, #23 |
| 43 | #27 | plan+build | #18, #25 |
| 44 | #28 | plan+build | #27, #25 |
