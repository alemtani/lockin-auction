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

A daily email at 3:00am PT. Every morning the slot planner (`season/planner.py`) makes the lock calls and picks today's lineup, with a plan for who tries each slot the rest of the week. Monday (or the week's first game day) adds the matchup; other days start with last night's games. Waiver pickups come every day; mid-week a pickup needs twice the Monday gain. `python3 season/backtest.py` replays finished weeks for every roster and compares the planner with the old calls.

- `season/daily.py`: the daily run. `--dry-run` prints the email. `--date YYYY-MM-DD` replays a day. `--send` emails it (needs `GMAIL_APP_PASSWORD`).
- `season/model.py`: each player starts at his projection and moves toward his real games. Steady veterans take about 11 games to reach 50/50; uncertain players switch faster.
- `season/planner.py`: the slot planner. Each player gets one home slot. A score locks when it beats what the rest of the roster gives up without him. A waiting score locks early when it beats that bar by 10 or more. A lock goes in the slot the fewest of your remaining players fit (the thinnest backup), and may bench a below-bar holder to get there. Waiting players stay in their slots, and a low score is benched only when someone who plays today needs the slot, or when a spare player who plays today (even one tagged Out) is worth more than that score. Waiver gains use the same slot math.
- `season/winprob.py`: the win-chance check, in shadow mode (it reports, it never changes a call). It scores each lock, roll and bench call by your chance to win the week instead of expected points, with the same lock math, and the email lists where the two disagree. Early in the week the swing still to come is large and it agrees; it speaks up late, when a sure score beats a slightly higher average. It needs a gain of 1 point of win chance for at most 2 expected points. Repository variables: `WIN_PROB=off` turns it off; `OPP_UNLOCKED` (default 0.05, for a league whose managers lock their big scores) is the chance an opponent's assumed lock wasn't made. Each check is saved in `data/season/winprob/`.
- `season/backtest.py`: replays finished weeks for all rosters. On weeks 1 to 3 the planner scored +13.8 per week over the old per-player calls, with about 17 lineup changes per week. `LOCK_NOW=99` turns early locks off.
- `data/season/`: game log, one belief snapshot per day (`history/`), each email (`out/`), and the planner's lock calls (`planner_locks/`).
- `data/season/locks.txt`: your own lock record, for days you did not follow the email. Sleeper's public data shows no locks, so the email assumes you followed every call.
- `data/season/opp_locks.txt`: your opponent's real locks, where the email guessed wrong. The email guesses their locks with the old per-player rule and lists each guess under **Their locks (assumed)**, flagged *check: riding paid* when a later game beat it. Their lineup is the best one from their whole roster (any game could have counted). A record on a game also means no earlier game was locked.
- Both files: one line per game, `date of the game|player name|lock or roll` (roll = that game was not locked). Add lines with the **lock record** action below (`season/add_lock.py`).
- Tests: `python3 -m unittest discover season`.

## GitHub actions

Run from the GitHub app: **Actions** > pick the action > **Run workflow**. Both share one queue (`concurrency: lockin-data`), so they never push at the same time.

### daily email (`.github/workflows/daily.yml`)

Fetches last night's games, updates every player, makes the lock calls and the lineup, emails them, and commits `data/season/`.

- **When:** cron-job.org starts it at 3:00am PT. GitHub's own schedule (3:12am PT) is the backup; it skips if it lands before 3am PT or today's email already went out.
- **By hand:** Run workflow. `force` sends again even if today's email already went out. Unit tests run first; a failing test stops the send.
- **Settings** (Settings > Secrets and variables > Actions):
  - Secret `GMAIL_APP_PASSWORD`: needed to send.
  - Variable `WIN_PROB`: `shadow` (default) reports the win-chance check, `off` removes it.
  - Variable `OPP_UNLOCKED`: chance an assumed opponent lock wasn't made, for the win-chance check (default 0.05). Locks you recorded in `opp_locks.txt` don't use it.

### lock record (`.github/workflows/lock.yml`)

Corrects a lock the email assumed, for you or your opponent, then resends today's email with the change.

| Input | What to put |
|---|---|
| `team` | `mine`: a call you didn't follow (writes `locks.txt`). `opponent`: a lock the email guessed wrong for them (writes `opp_locks.txt`). |
| `player` | Any part of the name, e.g. `giannis`. It must match one player on that team's roster this week. |
| `call` | `lock` or `roll` (roll = that game was not locked). |
| `date` | The game's date, `YYYY-MM-DD`. Blank = his latest game. For an opponent, use the date in the email's **Game** column (the game he didn't lock). |

- **Resend:** always, once today's email has gone out. Before that (after midnight, before the 3am run) the line is only saved and the morning email uses it.
- A line for the same player and date replaces the old one.
- **Example, your opponent rode a score:** the email shows *Giannis Antetokounmpo 54.5, Fri 11/13, since then 72.5 Sat 11/14, check: riding paid*, and the Sleeper app shows Giannis isn't locked. Run lock record with team `opponent`, player `giannis`, call `roll`, date `2026-11-13`.
- **Example, you rode against the call:** the email said lock Durant, you let him play. Team `mine`, player `durant`, call `roll`, date = the game he held that morning.
- From a terminal: `python3 season/add_lock.py --team opponent --player giannis --call roll --date 2026-11-13` (`--dry-run` prints the line and changes nothing).
