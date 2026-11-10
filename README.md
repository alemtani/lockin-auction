https://alemtani.github.io/lockin-auction/

# Lock-In Auction Assistant

Phone draft board for a 10-team Sleeper auction (Lock-In mode, $165 budget). For the player on the block it says **open at $X**, **bid $Y**, **hold**, or **stop**.

- `index.html`: the whole app (vanilla JS, no build). Sleeper calls run in the browser.
- `seed_values.csv`: player values (RotoWire + ESPN blend, built 2026-10-17). Values are fixed during the draft.
- `build_seed.py`: rebuilds the seed. `python3 build_seed.py --fetch` downloads fresh data first.

## Use

- Self-test: `?test=1` runs the PRD acceptance rows.
- Mock auction: `?draft=<mock draft id>`. Optional: `&me=<user id>`, `&league=<league id>`.
- Draft state lives in this browser's `localStorage`. Use ⋯ → Export to move it to another device.

## Flow on draft day

1. Type the player on the block. The card shows your max and an opening bid.
2. When someone bids, set the $ and tap their team. Two taps. The card says BID, HOLD, or STOP.
3. Sleeper sales arrive by polling every 5 s. If Sleeper is down, tap **SOLD** to record the sale by hand.

## Season (Phase 2)

A daily email at 5:00 PT. Every morning the slot planner (`season/planner.py`) makes the lock calls and picks today's lineup, with a plan for who tries each slot the rest of the week. Monday (or the week's first game day) adds the matchup; other days start with last night's games. Waiver pickups come every day; mid-week a pickup needs twice the Monday gain. `python3 season/backtest.py` replays finished weeks for every roster and compares the planner with the old calls.

- `season/daily.py`: the daily run. `--dry-run` prints the email. `--date YYYY-MM-DD` replays a day. `--send` emails it (needs `GMAIL_APP_PASSWORD`).
- `season/model.py`: each player starts at his projection and moves toward his real games. Steady veterans take about 11 games to reach 50/50; uncertain players switch faster.
- `season/planner.py`: the slot planner. Each player gets one home slot. A score locks when it beats what the rest of the roster gives up without him.
- `season/backtest.py`: replays finished weeks for all rosters. On weeks 1 to 3 the planner scored +12 per week over the old per-player calls.
- `data/season/`: game log, one belief snapshot per day (`history/`), each email (`out/`), and the planner's lock calls (`planner_locks/`).
- `data/season/locks.txt`: your own lock record, for days you did not follow the email. Sleeper does not share locks.
- Tests: `python3 -m unittest discover season`.
- `.github/workflows/daily.yml` runs it every morning and commits `data/season/`.
