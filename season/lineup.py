"""Lineups, bench swaps and waiver pickups. Pure functions."""

SLOTS = ['PG', 'SG', 'G', 'SF', 'PF', 'F', 'C', 'UTIL', 'UTIL']
SLOT_OK = {'PG': {'PG'}, 'SG': {'SG'}, 'G': {'PG', 'SG'}, 'SF': {'SF'}, 'PF': {'PF'}, 'F': {'SF', 'PF'}, 'C': {'C'}, 'UTIL': None}
BENCH_WEIGHT = 0.2  # a good bench is worth something in Lock-In: swaps during the week
BENCH_COUNT = 3


def fits(pos, slot):
    ok = SLOT_OK[slot]
    return ok is None or bool(ok & set(pos))


def best_lineup(players):
    """players: list of (pid, positions, value). Exact best legal 9.

    Returns (total of starters, {slot index: pid}).
    """
    full = (1 << len(SLOTS)) - 1
    dp = {0: (0.0, ())}  # filled slots mask -> (total, ((slot, pid), ...))
    for pid, pos, val in players:
        nxt = dict(dp)
        for mask, (tot, picks) in dp.items():
            for i, s in enumerate(SLOTS):
                if mask >> i & 1 or not fits(pos, s):
                    continue
                m2, t2 = mask | 1 << i, tot + val
                if m2 not in nxt or nxt[m2][0] < t2:
                    nxt[m2] = (t2, picks + ((i, pid),))
        dp = nxt
    tot, picks = max(dp.values(), key=lambda x: x[0]) if full not in dp else dp[full]
    return tot, dict(picks)


def team_value(players):
    """Starters plus a little for the best bench players."""
    tot, picks = best_lineup(players)
    used = set(picks.values())
    bench = sorted((v for pid, _, v in players if pid not in used), reverse=True)[:BENCH_COUNT]
    return tot + BENCH_WEIGHT * sum(bench), picks


def swap_gain(starter_value, bench_value):
    """Points gained by moving a bench player into a starter's slot for the rest of the week."""
    return bench_value - starter_value
