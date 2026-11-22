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
| 12 | #67 | plan+build |  |
| 13 | #45 | build |  |
| 14 | #8 | plan+build |  |
| 15 | #47 | plan+build | #8 |
| 16 | #9 | plan+build |  |
| 17 | #48 | plan+build |  |
| 18 | #16 | plan+build | #48 |
| 19 | #13 | issue |  |
| 20 | #14 | issue | #13 |
| 21 | #11 | issue |  |
| 22 | #12 | issue | #11 |
| 23 | #33 | issue | #6, #9, #13 |
| 24 | #24 | issue |  |
| 25 | #15 | plan+build |  |
| 26 | #29 | plan+build |  |
| 27 | #68 | issue | #21 |
| 28 | #32 | plan+build | #29, #9, #15 |
| 29 | #21 | issue | #16 |
| 30 | #22 | issue | #16 |
| 31 | #23 | build |  |
| 32 | #51 | plan+build | #23 |
| 33 | #54 | plan+build | #23 |
| 34 | #55 | plan+build | #54 |
| 35 | #49 | issue |  |
| 36 | #17 | issue |  |
| 37 | #36 | issue |  |
| 38 | #37 | issue |  |
| 39 | #44 | issue |  |
| 40 | #52 | issue |  |
| 41 | #18 | plan+build | M1 |
| 42 | #30 | plan+build | #18 |
| 43 | #25 | plan+build | #18, #9, #15 |
| 44 | #31 | plan+build | #18 |
| 45 | #35 | plan+build | #18, #23 |
| 46 | #27 | plan+build | #18, #25 |
| 47 | #28 | plan+build | #27, #25 |
