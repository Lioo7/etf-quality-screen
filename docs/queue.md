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
| 18 | #78 | plan+build |  |
| 19 | #9 | plan+build |  |
| 20 | #13 | issue |  |
| 21 | #33 | issue | #6, #9, #13 |
| 22 | #48 | plan+build |  |
| 23 | #16 | plan+build | #48 |
| 24 | #14 | issue | #13 |
| 25 | #11 | issue |  |
| 26 | #12 | issue | #11 |
| 27 | #24 | issue |  |
| 28 | #15 | plan+build |  |
| 29 | #29 | plan+build |  |
| 30 | #32 | plan+build | #29, #9, #15 |
| 31 | #21 | issue | #16 |
| 32 | #68 | issue | #21 |
| 33 | #22 | issue | #16 |
| 34 | #23 | build |  |
| 35 | #51 | plan+build | #23 |
| 36 | #54 | plan+build | #23 |
| 37 | #55 | plan+build | #54 |
| 38 | #49 | issue |  |
| 39 | #17 | issue |  |
| 40 | #36 | issue |  |
| 41 | #37 | issue |  |
| 42 | #44 | issue |  |
| 43 | #52 | issue |  |
| 44 | #18 | plan+build | M1 |
| 45 | #30 | plan+build | #18 |
| 46 | #25 | plan+build | #18, #9, #15 |
| 47 | #31 | plan+build | #18 |
| 48 | #35 | plan+build | #18, #23 |
| 49 | #27 | plan+build | #18, #25 |
| 50 | #28 | plan+build | #27, #25 |
