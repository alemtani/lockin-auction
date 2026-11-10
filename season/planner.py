"""Daily slot planner for Lock-In (Batch 2 prototype). Pure functions. No network, no files.

Rules (confirmed by the user 2026-11-09):
  A score belongs to the player. His latest started game counts unless he locked an earlier one.
  Move him to another starting slot: his score goes with him. Move him to the bench: it is gone.
  A game counts only if he started. A locked player stays in his slot for the rest of the week.
  A score can be locked any time before the player's next game.
  You act once each morning, when no game is in progress, so every slot move is legal then.

The idea: each slot is its own lock problem. Its games are whoever we plan to start there each day,
so the lock math in model.LockIn works unchanged on a slot's planned games. A slot that can take
anyone (UTIL) gets the best games, so its bar is high. A slot with few candidates (C) has a low bar.
That is why a score is locked in the most restrictive slot where it clears the bar.

players (the planning view): pid -> dict(name, pos, games={date: (mu, a, sigma)}), games from today on.
"""
import lineup
import model

SLOTS = lineup.SLOTS


def assign(rows, slots):
    """Best legal fill of some slots. rows: (pid, positions, value). May leave slots empty."""
    idx = {s: j for j, s in enumerate(slots)}
    dp = {0: (0.0, ())}
    for pid, pos, val in rows:
        if val <= 0:
            continue
        nxt = dict(dp)
        for mask, (tot, picks) in dp.items():
            for s in slots:
                j = idx[s]
                if mask >> j & 1 or not lineup.fits(pos, SLOTS[s]):
                    continue
                m2, t2 = mask | 1 << j, tot + val
                if m2 not in nxt or nxt[m2][0] < t2 - 1e-9:
                    nxt[m2] = (t2, picks + ((s, pid),))
        dp = nxt
    return dict(max(dp.values(), key=lambda x: x[0])[1])


def merged(members, players, days):
    """A slot's planned games: one per day, from the member with the most expected points that day."""
    out = []
    for d in days:
        best = max(((players[p]['games'][d][0] * players[p]['games'][d][1], p) for p in members if d in players[p]['games']),
                   default=None)
        if best:
            out.append((best[1], d) + tuple(players[best[1]]['games'][d]))
    return out


def streams(players, days, slots, exclude=()):
    """Planned games per slot from `days` on: slot -> [(pid, date, mu, a, sigma), ...].

    Each player gets one home slot, so his games count toward one slot only. (Letting every slot count on
    every player's games made the plan worth more than the best possible week, so it almost never locked.)
    First the best legal starter per slot, then each other player joins the slot where he adds the most.
    """
    pool = [p for p in players if p not in exclude and any(d in players[p]['games'] for d in days)]
    own = {p: fresh_value(merged([p], players, days)) for p in pool}
    home = {s: [] for s in slots}
    first = assign([(p, players[p]['pos'], own[p]) for p in pool], slots)
    for s, p in first.items():
        home[s].append(p)
    cur = {s: fresh_value(merged(home[s], players, days)) for s in slots}
    for p in sorted((p for p in pool if p not in first.values()), key=lambda p: -own[p]):
        best = None
        for s in slots:
            if lineup.fits(players[p]['pos'], SLOTS[s]):
                v = fresh_value(merged(home[s] + [p], players, days))
                if best is None or v - cur[s] > best[0]:
                    best = (v - cur[s], s, v)
        if best and best[0] > 0:
            home[best[1]].append(p)
            cur[best[1]] = best[2]
    return {s: merged(home[s], players, days) for s in slots}


def _lockin(stream):
    return model.LockIn(0, 0, 0, len(stream), mus=[g[2] for g in stream], avail=[g[3] for g in stream],
                        sigmas=[g[4] for g in stream])


_FV = {}


def fresh_value(stream):
    """What the slot is worth from its first planned game if it holds no score now."""
    if not stream:
        return 0.0
    key = tuple(g[2:] for g in stream)
    if key not in _FV:
        _FV[key] = _lockin(stream).week_value()
    return _FV[key]


def total(players, days, slots, exclude=()):
    """What these open slots are worth together from `days` on, with no scores held."""
    return sum(fresh_value(g) for g in streams(players, days, slots, exclude).values())


def lock_options(p, s, days, holder, score, locked, players):
    """For a holder p in slot s with an unlocked score: [(bar, slot t)] where he can lock, bar = the score he needs.

    days: game days from today on. If he rolls, his games from today on are tries again.
    Bar = what the other open slots lose without him and without slot t. The slot that costs the rest of
    the roster least has the lowest bar, which is usually the most restrictive slot he fits. A move needs
    the other slot's holder to have no score, or to fit back in slot s.
    """
    open_ = [t for t in range(len(SLOTS)) if t not in locked]
    pinned = {holder[t] for t in locked}
    pool = {q: v for q, v in players.items() if q not in pinned}
    base = total(pool, days, open_)
    out = []
    for t in open_:
        q = holder.get(t)
        movable = t == s or q is None or q == p or q not in score or lineup.fits(players[q]['pos'], SLOTS[s])
        if lineup.fits(players[p]['pos'], SLOTS[t]) and movable:
            out.append((base - total(pool, days, [u for u in open_ if u != t], exclude={p}), t))
    return sorted(out)


def morning(day, days, holder, score, locked, players, tie=0.0, safe=True):
    """The morning move: lock calls, then today's starter for every open slot.

    holder: slot -> pid. score: pid -> current score (started players only). locked: set of slots.
    days: the week's game days from today on. Returns (new holder, notes per slot, calls).
    tie, safe: a close call is a lock within `tie` points of its bar. safe (the favorite) locks it,
    otherwise (the underdog) rolls it. The same rule as the lineup and the old lock calls.

    1. Lock calls, only for holders who play today: lock now or today's game replaces the score.
       Lock in slot t when his score plus the plan for the other open slots (without him) beats the plan
       with him rolling. A holder with no game today needs no call yet: his score waits.
    2. A holder with a score and no game today keeps it if his score plus the rest of the plan without him
       beats the plan with him back in the pool. He sits in the slot where that works best.
    3. Every other open slot gets the plan's game for today, and an empty slot takes any game today
       (a free try: a bad score costs nothing, you can still replace it).
    calls: one dict per holder with an unlocked score: kind = lock, roll, keep (no game today, it waits)
    or drop (benched: a fresh try is worth more), with score x, bar, slot and from_slot.
    Changes holder and locked in place.
    """
    calls = []
    for x, p in sorted(((score[p], p) for s, p in holder.items() if s not in locked and p in score
                        and day in players[p]['games']), reverse=True):
        s = next((t for t, q in holder.items() if q == p and t not in locked), None)  # he may have moved
        if s is None:
            continue
        opts = [(x - bar, bar, t) for bar, t in lock_options(p, s, days, holder, score, locked, players)]
        if not opts:
            continue
        gap, bar, t = max(opts)
        close = abs(gap) < tie
        if gap < (-tie if safe else tie):
            calls.append(dict(p=p, kind='roll', x=x, bar=bar, slot=t, from_slot=s, close=close))
            continue
        if t != s:
            q = holder.get(t)
            holder[t] = p
            if q is not None and lineup.fits(players[q]['pos'], SLOTS[s]):
                holder[s] = q
            else:
                del holder[s]
                score.pop(q, None)
        locked.add(t)
        calls.append(dict(p=p, kind='lock', x=x, bar=bar, slot=t, from_slot=s, close=close))

    open_ = [s for s in range(len(SLOTS)) if s not in locked]
    pinned = {holder[s] for s in locked}
    pool = {p: v for p, v in players.items() if p not in pinned}
    new = {s: holder[s] for s in locked}
    used = set(pinned)
    keep = sorted(((score[p], p) for s, p in holder.items() if s in open_ and p in score and p in pool
                   and day not in pool[p]['games']), reverse=True)
    free = list(open_)
    for x, p in keep:
        avail = {q: v for q, v in pool.items() if q not in used}
        base = total(avail, days, free)
        opts = [(x + total(avail, days, [t for t in free if t != s], exclude={p}), s) for s in free
                if lineup.fits(pool[p]['pos'], SLOTS[s])]
        if opts and max(opts)[0] >= base:
            s = max(opts)[1]
            new[s] = p
            used.add(p)
            free.remove(s)
            calls.append(dict(p=p, kind='keep', x=x, bar=base - (max(opts)[0] - x), slot=s, close=False))
        else:
            calls.append(dict(p=p, kind='drop', x=x, bar=base - (max(opts)[0] - x) if opts else None, slot=None, close=False))
    st = streams({q: v for q, v in pool.items() if q not in used}, days, free)
    for s in free:  # the plan's game for today
        if st[s] and st[s][0][1] == day and st[s][0][0] not in used:
            new[s] = st[s][0][0]
            used.add(new[s])
    left = [s for s in free if s not in new]
    rows = [(p, v['pos'], v['games'][day][0] * v['games'][day][1]) for p, v in pool.items()
            if p not in used and day in v['games']]
    for s, p in assign(rows, [s for s in left if holder.get(s) not in score]).items():
        new[s] = p
        used.add(p)
    for s in left:  # nobody plays here today: no reason to bench the holder
        if s not in new and holder.get(s) and holder[s] not in used:
            new[s] = holder[s]
            used.add(holder[s])
    notes = {s: dict(worth=fresh_value(st[s]), plan=st[s]) for s in free}
    for c in calls:  # a dropped holder can still end up starting (nobody else plays his slot today)
        if c['kind'] == 'drop' and c['p'] in new.values():
            c.update(kind='keep', slot=next(s for s, q in new.items() if q == c['p']))
    return new, notes, calls
