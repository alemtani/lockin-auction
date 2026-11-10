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
    the other slot's holder to have no score, to fit back in slot s, or to hold a score below his own bar
    (he would be benched anyway, so the move benches him).
    """
    open_ = [t for t in range(len(SLOTS)) if t not in locked]
    pinned = {holder[t] for t in locked}
    pool = {q: v for q, v in players.items() if q not in pinned}
    base = total(pool, days, open_)

    def below_bar(q):
        """q's score loses to a fresh try: the rest of the roster without him and his slot beats it."""
        rest = [total(pool, days, [u for u in open_ if u != w], exclude={q}) for w in open_
                if lineup.fits(players[q]['pos'], SLOTS[w])]
        return not rest or score[q] + max(rest) < base
    out = []
    for t in open_:
        q = holder.get(t)
        movable = (t == s or q is None or q == p or q not in score or lineup.fits(players[q]['pos'], SLOTS[s])
                   or below_bar(q))
        if lineup.fits(players[p]['pos'], SLOTS[t]) and movable:
            out.append((base - total(pool, days, [u for u in open_ if u != t], exclude={p}), t))
    return sorted(out)


LOCK_NOW = 10.0  # points: a waiting score this far above its bar locks now, so a missed morning can't lose it
SLOT_TIE = 1.0   # points: lock in the most restrictive slot unless another slot's bar is lower by more than this
MOVE_TIE = 1.0   # points: a waiting player moves to another slot only if that is worth more than this


def rank(s, backups=None):
    """How restrictive a slot is. First: how many of your players who still play this week fit it
    (fewer = more restrictive, so the slot with the thinnest backup gets the lock). Then the slot itself:
    C, PG, SG, SF, PF (1 position) before G, F (2) before UTIL."""
    ok = lineup.SLOT_OK[SLOTS[s]]
    n = sum(lineup.fits(pos, SLOTS[s]) for pos in backups) if backups is not None else 0
    return n, len(ok) if ok else 99


def pick(opts, backups=None):
    """opts: [(gap, bar, slot)]. The most restrictive slot within SLOT_TIE of the best gap.
    backups: positions of your unpinned players who still have a game this week."""
    g = max(o[0] for o in opts)
    return min((o for o in opts if o[0] >= g - SLOT_TIE), key=lambda o: (rank(o[2], backups), -o[0]))


def seat(rows, slots, cur, held):
    """Seat every player in rows (pid, positions) in `slots`, moving as few as possible. Returns slot -> pid.

    cur: slot -> pid now. held: pid -> score he holds now. Seated players count most, then players left in
    their own slot. Then a slot with no score holder before one with a holder, and a low score before a high one,
    so the holders who stay keep the most.
    """
    idx = {t: j for j, t in enumerate(slots)}
    dp = {0: (0.0, ())}
    for pid, pos in rows:
        nxt = {}
        for mask, (tot, picks) in dp.items():
            for t in slots:
                j = idx[t]
                if mask >> j & 1 or not lineup.fits(pos, SLOTS[t]):
                    continue
                q = cur.get(t)
                v = tot + 1000 + (10 if q == pid else 0) - (1 + held[q] / 100 if q in held and q != pid else 0)
                m2 = mask | 1 << j
                if m2 not in nxt or nxt[m2][0] < v:
                    nxt[m2] = (v, picks + ((t, pid),))
        dp = nxt or dp
    return dict(max(dp.values(), key=lambda x: x[0])[1])


def morning(day, days, holder, score, locked, players, tie=0.0, safe=True, lock_now=LOCK_NOW):
    """The morning move: lock calls, then today's starter for every open slot, with as few moves as possible.

    holder: slot -> pid. score: pid -> current score (started players only). locked: set of slots.
    days: the week's game days from today on. Returns (new holder, notes per slot, calls).
    tie, safe: a close call is a lock within `tie` points of its bar. safe (the favorite) locks it,
    otherwise (the underdog) rolls it. The same rule as the lineup and the old lock calls.

    1. Lock calls, highest score first. A holder who plays today: lock now, or today's game replaces the score.
       A holder with no game today locks now only if his score beats the bar by `lock_now` or more.
       Otherwise it waits: no call is needed before his next game. Bar = what the other open slots lose
       without him and without the slot he locks. He locks in the most restrictive slot unless another costs
       clearly less (SLOT_TIE). Each call sees the locks made before it.
    2. A waiting holder whose score beats his bar ('keep') stays in his slot. He moves only if another slot
       is worth more than MOVE_TIE more for the rest of the roster.
    3. Today's games: the plan's game for each other open slot, then free tries for empty slots.
       They take open slots with as few moves as possible.
    4. A waiting holder below his bar keeps his score only while his slot is not needed today ('hold').
       If a player who plays today needs it, he is benched and the score is gone ('drop').
       A starter with nothing to do today stays where he is.
    calls: one dict per holder with an unlocked score: kind = lock (now=True if he has no game today), roll,
    keep, hold or drop, with score x, bar, slot, from_slot, and for hold/drop the slot that plans his games.
    Changes holder and locked in place.
    """
    calls = []
    for x, p in sorted(((score[p], p) for s, p in holder.items() if s not in locked and p in score), reverse=True):
        s = next((t for t, q in holder.items() if q == p and t not in locked), None)  # he may have moved
        if s is None:
            continue
        plays = day in players[p]['games']
        opts = [(x - bar, bar, t) for bar, t in lock_options(p, s, days, holder, score, locked, players)]
        if not opts:
            continue
        pinned = {holder[u] for u in locked}
        backups = [v['pos'] for q, v in players.items() if q not in pinned and any(d in v['games'] for d in days)]
        gap, bar, t = pick(opts, backups)
        close = abs(gap) < tie
        if plays and gap < (-tie if safe else tie):
            calls.append(dict(p=p, kind='roll', x=x, bar=bar, slot=t, from_slot=s, close=close))
            continue
        if not plays and gap < lock_now:
            continue  # it waits; step 2 decides where he sits today
        if t != s:
            q = holder.get(t)
            holder[t] = p
            if q is not None and lineup.fits(players[q]['pos'], SLOTS[s]):
                holder[s] = q
            else:
                del holder[s]
                if q in score:
                    calls.append(dict(p=q, kind='drop', x=score[q], bar=None, slot=None, from_slot=t, close=False,
                                      plan_slot=None, by=p))
                score.pop(q, None)
        locked.add(t)
        calls.append(dict(p=p, kind='lock', x=x, bar=bar, slot=t, from_slot=s, close=close and plays, now=not plays))

    open_ = [s for s in range(len(SLOTS)) if s not in locked]
    pinned = {holder[s] for s in locked}
    pool = {p: v for p, v in players.items() if p not in pinned}
    new = {s: holder[s] for s in locked}
    used = set(pinned)
    cur = {s: holder[s] for s in open_ if s in holder}
    waiting = sorted(((score[p], p, s) for s, p in cur.items() if p in score and p in pool
                      and day not in pool[p]['games']), reverse=True)
    free = list(open_)
    below = []
    for x, p, s0 in waiting:
        avail = {q: v for q, v in pool.items() if q not in used}
        base = total(avail, days, free)
        opts = {s: x + total(avail, days, [t for t in free if t != s], exclude={p}) for s in free
                if lineup.fits(pool[p]['pos'], SLOTS[s])}
        best = max(opts, key=opts.get) if opts else None
        if best is None or opts[best] < base:
            below.append((x, p, s0, base - (opts[best] - x) if opts else None))
            continue
        s = s0 if s0 in opts and opts[s0] >= opts[best] - MOVE_TIE else best
        new[s] = p
        used.add(p)
        free.remove(s)
        calls.append(dict(p=p, kind='keep', x=x, bar=base - (opts[s] - x), slot=s, from_slot=s0, close=False))
    held = dict(score)
    st = streams({q: v for q, v in pool.items() if q not in used}, days, free)
    today = []
    for s in free:  # the plan's game for today
        if st[s] and st[s][0][1] == day and st[s][0][0] not in used:
            today.append((st[s][0][0], s))
            used.add(st[s][0][0])
    left = [s for s in free if s not in {t for _, t in today}]
    rows = [(p, v['pos'], v['games'][day][0] * v['games'][day][1]) for p, v in pool.items()
            if p not in used and day in v['games']]
    for s, p in assign(rows, [s for s in left if cur.get(s) not in held]).items():  # free tries
        today.append((p, s))
        used.add(p)
    new.update(seat([(p, pool[p]['pos']) for p, _ in today], free, cur, held))
    for s in free:  # nobody plays here today: the starter stays (a holder below his bar keeps his score for now)
        if s not in new and cur.get(s) and cur[s] not in used and cur[s] in pool:
            new[s] = cur[s]
            used.add(cur[s])
    home = {g[0]: s for s in free for g in st[s]}
    for x, p, s0, bar in below:
        on = next((s for s, q in new.items() if q == p), None)
        calls.append(dict(p=p, kind='hold' if on is not None else 'drop', x=x, bar=bar, slot=on, from_slot=s0,
                          close=False, plan_slot=home.get(p)))
    notes = {s: dict(worth=fresh_value(st[s]), plan=st[s]) for s in free}
    return new, notes, calls
