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
| 15 | #8 | plan+build |  |
| 16 | #47 | plan+build | #8 |
| 17 | #9 | plan+build |  |
| 18 | #48 | plan+build |  |
| 19 | #16 | plan+build | #48 |
| 20 | #13 | issue |  |
| 21 | #14 | issue | #13 |
| 22 | #11 | issue |  |
| 23 | #12 | issue | #11 |
| 24 | #33 | issue | #6, #9, #13 |
| 25 | #24 | issue |  |
| 26 | #15 | plan+build |  |
| 27 | #29 | plan+build |  |
| 28 | #32 | plan+build | #29, #9, #15 |
| 29 | #21 | issue | #16 |
| 30 | #68 | issue | #21 |
| 31 | #22 | issue | #16 |
| 32 | #23 | build |  |
| 33 | #51 | plan+build | #23 |
| 34 | #54 | plan+build | #23 |
| 35 | #55 | plan+build | #54 |
| 36 | #49 | issue |  |
| 37 | #17 | issue |  |
| 38 | #36 | issue |  |
| 39 | #37 | issue |  |
| 40 | #44 | issue |  |
| 41 | #52 | issue |  |
| 42 | #18 | plan+build | M1 |
| 43 | #30 | plan+build | #18 |
| 44 | #25 | plan+build | #18, #9, #15 |
| 45 | #31 | plan+build | #18 |
| 46 | #35 | plan+build | #18, #23 |
| 47 | #27 | plan+build | #18, #25 |
| 48 | #28 | plan+build | #27, #25 |
