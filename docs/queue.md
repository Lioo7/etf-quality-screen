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
| 10 | #62 | plan+build | #58 |
| 11 | #20 | build | #6 |
| 12 | #45 | build |  |
| 13 | #8 | plan+build |  |
| 14 | #47 | plan+build | #8 |
| 15 | #9 | plan+build |  |
| 16 | #48 | plan+build |  |
| 17 | #16 | plan+build | #48 |
| 18 | #13 | issue |  |
| 19 | #14 | issue | #13 |
| 20 | #11 | issue |  |
| 21 | #12 | issue | #11 |
| 22 | #33 | issue | #6, #9, #13 |
| 23 | #24 | issue |  |
| 24 | #15 | plan+build |  |
| 25 | #29 | plan+build |  |
| 26 | #32 | plan+build | #29, #9, #15 |
| 27 | #21 | issue | #16 |
| 28 | #22 | issue | #16 |
| 29 | #23 | build |  |
| 30 | #51 | plan+build | #23 |
| 31 | #54 | plan+build | #23 |
| 32 | #55 | plan+build | #54 |
| 33 | #49 | issue |  |
| 34 | #17 | issue |  |
| 35 | #36 | issue |  |
| 36 | #37 | issue |  |
| 37 | #44 | issue |  |
| 38 | #52 | issue |  |
| 39 | #18 | plan+build | M1 |
| 40 | #30 | plan+build | #18 |
| 41 | #25 | plan+build | #18, #9, #15 |
| 42 | #31 | plan+build | #18 |
| 43 | #35 | plan+build | #18, #23 |
| 44 | #27 | plan+build | #18, #25 |
| 45 | #28 | plan+build | #27, #25 |
