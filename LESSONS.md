# Lessons from the 2026 draft

Draft: Sun 2026-10-18. 10 teams, $165, 14 spots, Lock-In mode. I was slot 10.

## Result

I spent $162 and filled all 14 spots. No player cost more than $29.

Top-9 weekly projection after the draft. Open spots on unfinished teams count as 30 per week. "Blend" mixes the seed value with a market value taken from Sleeper ADP rank.

| Market weight | Me | Best other team | My place |
|---|---|---|---|
| 0% (seed only) | 394 | 361 (Rohan312) | 1st by 33 |
| 50% | 375 | 366 (Rohan312) | 1st by 9 |
| 100% (market only) | 363 | 373 (Rohan312) | 2nd by 10 |

Rohan312 used the same plan: no big early buys, full roster. The teams that paid $65–70 per star (Wemby, Luka, SGA, Jokić) finished 6th to 10th. They filled most spots with $1 players.

Best buys: Austin Reaves $13, Kyrie Irving $9, Markkanen, Lillard and Allen $5 each, Michael Porter $3.

Risk: AD and Sabonis each played about 20 games in 2025-26. Kyrie and Lillard played 0. The seed guessed their game counts.

## Strategy lessons

1. **Don't buy stars early.** Let other teams pay $65–70. Each overpay removes a buyer from the rest of the draft. The middle tier then goes cheap.
2. **Fill every spot with real players.** Depth beat stars on draft day. In Lock-In mode, depth also gives more games to choose from.
3. **Trust the market on injury risk.** The players where my seed and the market disagreed most were the injury cases. The market was right to discount them.
4. **Go above your max when the room is broke.** When 5+ teams have overspent, only 2–3 teams can still bid. My money was worth less to me than the league average. Paying market value for a target was right, because the broke teams could not compete.
5. **Finish near $0.** Unspent money is worth nothing.
6. **Spreading money works only when the room overpays at the top.** In a disciplined room, the middle tier costs more and the advantage shrinks.

## Changes for next season

### 1. Blend the seed with the market

Mix each player's seed value with a market value, about 50/50. Market value: the seed value of the player at the same rank as his ADP. Put this in `build_seed.py` so the app needs no change.

Why: it pulls down the injury cases (Kyrie ADP 110, Lillard ADP 147) and changes little for healthy players. With 50%, I still ranked 1st, by a smaller and more honest margin.

### 2. Count only teams that can still bid

`leagueState` in `index.html` sets `mult = max(inflation, pm)`.

- `inflation` uses the money left in the whole league. Broke teams still count, but they can bid only $1–8.
- `pm` compares my spendable money to the value of the top `2 × my spots` unsold players. Early in the draft that is 28 players, so `pm` stays low until late.

Fix one or both:

- **Leave out broke teams.** In `inflation`, skip a team's money once its `maxBid` falls below about $5. Skip its open spots too.
- **Make `pm` fair early.** Compare my money per open spot to the league average for teams that can still bid. Cap it at about 1.3× the league `inflation`.

Either change would have raised my maxes in the middle of the draft, when I had to override the app by hand.

Test: run `?test=1` and add a row with 5 broke teams. My max for a target should rise above the current value.
