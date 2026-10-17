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
