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
| 13 | #71 | plan+build |  |
| 14 | #45 | build |  |
| 15 | #74 | plan+build |  |
| 16 | #8 | plan+build |  |
| 17 | #47 | plan+build | #8 |
| 18 | #9 | plan+build |  |
| 19 | #48 | plan+build |  |
| 20 | #16 | plan+build | #48 |
| 21 | #13 | issue |  |
| 22 | #14 | issue | #13 |
| 23 | #11 | issue |  |
| 24 | #12 | issue | #11 |
| 25 | #33 | issue | #6, #9, #13 |
| 26 | #24 | issue |  |
| 27 | #15 | plan+build |  |
| 28 | #29 | plan+build |  |
| 29 | #32 | plan+build | #29, #9, #15 |
| 30 | #21 | issue | #16 |
| 31 | #68 | issue | #21 |
| 32 | #22 | issue | #16 |
| 33 | #23 | build |  |
| 34 | #51 | plan+build | #23 |
| 35 | #54 | plan+build | #23 |
| 36 | #55 | plan+build | #54 |
| 37 | #49 | issue |  |
| 38 | #17 | issue |  |
| 39 | #36 | issue |  |
| 40 | #37 | issue |  |
| 41 | #44 | issue |  |
| 42 | #52 | issue |  |
| 43 | #18 | plan+build | M1 |
| 44 | #30 | plan+build | #18 |
| 45 | #25 | plan+build | #18, #9, #15 |
| 46 | #31 | plan+build | #18 |
| 47 | #35 | plan+build | #18, #23 |
| 48 | #27 | plan+build | #18, #25 |
| 49 | #28 | plan+build | #27, #25 |
