"""Backtest: old lock calls vs the slot planner, on finished weeks, for every roster in the league.

  python3 season/backtest.py            weeks 1 to (current week - 1)

Uses real games from data/season/games.csv (written by daily.py). Each player's estimate is frozen at the
start of the week from games before it. Caveats: today's rosters for every week; no injury news from the
past, so a player who missed his team's last game is treated as likely out (both policies get this).

Policies, all with the same estimates and the same real games:
  set and forget: Monday's best lineup, never changed; each player locks on his own bar.
  old email:      re-picks the lineup each day by player value; each player locks on his own bar.
  planner:        slot planner. Each morning: lock calls (a score can wait until the player's next game),
                  then one lineup for the day.
  hindsight:      each slot gets the best game any eligible player had (the ceiling, not reachable).
"""
import csv, datetime, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import fetch, lineup, model, planner

MU_SHIFT = float(os.environ.get('MU_SHIFT', 0))  # experiment: points added to every estimate
HEALTHY_A = 0.93  # chance he plays when he played his team's last game
MISSED_A = (0.3, 0.6)  # missed his team's last game: next game, later games

SL = lineup.SLOTS
BIAS = []  # (estimate, real score) for every rostered game
ENDS = defaultdict(lambda: defaultdict(float))  # policy -> locked / latest / empty slot counts, summed


def load():
    games = defaultdict(dict)  # pid -> date -> fp
    for r in csv.DictReader(open('data/season/games.csv'), delimiter='|'):
        games[r['sleeper_id']][r['date']] = (float(r['fp']), float(r['min']))
    sched = json.load(open('data/season/raw/schedule.json'))
    players = json.load(open('data/season/raw/players.json'))
    league = json.load(open('data/season/raw/league.json'))
    rosters = json.load(open('data/season/raw/rosters.json'))
    return games, sched, players, league, rosters


def tally(name, holder, score, locked):
    e = ENDS[name]
    for s in range(len(SL)):
        p = holder.get(s)
        x = score.get(p) if p else None
        kind = 'empty' if x is None else 'locked' if s in locked else 'latest'
        e[kind] += 1
        e[kind + '_pts'] += x or 0
    e['weeks'] += 1


def run():
    games, sched, players, league, rosters = load()
    scoring = {k: v for k, v in league['scoring_settings'].items() if v}
    import daily
    priors = daily.build_priors(players, scoring)
    weeks = defaultdict(set)
    team_dates = defaultdict(set)
    for g in sched:
        weeks[g['week']].add(g['date'])
        for side in ('home', 'away'):
            team_dates[g[side]['team']].add(g['date'])
    today = datetime.date.today().isoformat()
    done = [w for w in sorted(weeks) if max(weeks[w]) < today]

    results = defaultdict(dict)
    for wk in done:
        dates = sorted(weeks[wk])
        start = dates[0]
        for r in rosters:
            ids = [p for p in (r['players'] or []) if p not in (r.get('reserve') or []) and (players.get(p) or {}).get('team')]
            info = {}
            for p in ids:
                pl = players[p]
                gs = [dict(fp=fp, min=m) for d, (fp, m) in sorted(games.get(p, {}).items()) if d < start]
                played_before = len([d for d in team_dates[pl['team']] if d < start])
                b = model.belief(priors.get(p) or model.prior_unknown(), gs, played_before, '')
                info[p] = dict(name=pl.get('full_name', p), pos=pl.get('fantasy_positions') or [], team=pl['team'], mu=b['mu'] + MU_SHIFT, sigma=b['sigma'])
                BIAS.extend((b['mu'], games[p][d][0]) for d in dates if d in games.get(p, {}))
            actual = {(p, d): games[p][d][0] for p in ids for d in dates if d in games.get(p, {})}

            def view(day):
                """Planning view from `day` on."""
                d0 = day
                out = {}
                for p, v in info.items():
                    tds = sorted(team_dates[v['team']])
                    past = [d for d in tds if d < d0]
                    missed = bool(past) and past[-1] >= dates[0] and (p, past[-1]) not in actual
                    if past and past[-1] < dates[0]:  # last week's last game
                        missed = past[-1] not in games.get(p, {})
                    gs = {}
                    future = [d for d in tds if d in weeks[wk] and d >= d0]
                    for i, d in enumerate(future):
                        a = (MISSED_A[0] if i == 0 else MISSED_A[1]) if missed else HEALTHY_A
                        gs[d] = (v['mu'], a, v['sigma'])
                    out[p] = dict(name=v['name'], pos=v['pos'], games=gs)
                return out

            # ---- planner ----
            def plan():
                holder, score, locked = {}, {}, set()
                for i, d in enumerate(dates):
                    V = view(d)
                    holder, _, _ = planner.morning(d, dates[i:], holder, score, locked, V)
                    for p in list(score):
                        if p not in holder.values():
                            del score[p]
                    for s, p in holder.items():
                        x = actual.get((p, d))
                        if s not in locked and x is not None and d in V[p]['games']:
                            score[p] = x
                tally('plan', holder, score, locked)
                return sum(score.get(p, 0) for p in holder.values())

            # ---- old email and set-and-forget: player-level lock bars ----
            def old(rechoose):
                holder, score, locked = {}, {}, set()
                for i, d in enumerate(dates):
                    V = view(d)
                    pinned = {holder[s] for s in locked}

                    def pval(p):
                        g = V[p]['games']
                        if not g:
                            return score.get(p, 0) if p in holder.values() else 0
                        L = model.LockIn(0, 0, 0, len(g), mus=[x[0] for x in g.values()], avail=[x[1] for x in g.values()],
                                         sigmas=[x[2] for x in g.values()])
                        return L.week_value(current=score[p]) if p in score and p in holder.values() else L.week_value()
                    if rechoose or i == 0:
                        open_ = [s for s in range(len(SL)) if s not in locked]
                        rows = [(p, V[p]['pos'], pval(p)) for p in V if p not in pinned]
                        new = {s: holder[s] for s in locked}
                        new.update(planner.assign(rows, open_))
                        holder = new
                        for p in list(score):
                            if p not in holder.values():
                                del score[p]
                    for s, p in holder.items():
                        if s in locked or d not in V[p]['games']:
                            continue
                        x = actual.get((p, d))
                        if x is None:
                            continue
                        score[p] = x
                        g = V[p]['games']
                        after = [k for k in g if k > d]
                        if after:
                            L = model.LockIn(0, 0, 0, len(g), mus=[g[k][0] for k in g], avail=[g[k][1] for k in g],
                                             sigmas=[g[k][2] for k in g])
                            if x >= L.threshold(len(after)):
                                locked.add(s)
                tally('old' if rechoose else 'forget', holder, score, locked)
                return sum(score.get(p, 0) for p in holder.values())

            best = {p: max((actual[p, d] for d in dates if (p, d) in actual), default=0) for p in ids}
            hind = sum(best[p] for p in planner.assign([(p, info[p]['pos'], best[p]) for p in ids], list(range(len(SL)))).values())
            results[wk][r['roster_id']] = dict(forget=old(False), old=old(True), plan=plan(), hind=hind)
    return results, rosters


def main():
    results, rosters = run()
    users = {u['user_id']: (u.get('metadata') or {}).get('team_name') or u['display_name'] for u in json.load(open('data/season/raw/users.json'))}
    owner = {r['roster_id']: users.get(r['owner_id'], str(r['roster_id'])) for r in rosters}
    me = next(r['roster_id'] for r in rosters if r['owner_id'] == fetch.ME)
    cols = [('forget', 'Set and forget'), ('old', 'Old email'), ('plan', 'Planner'), ('hind', 'Hindsight')]
    print(f'Estimate vs real, rostered games: estimate {sum(a for a, _ in BIAS) / len(BIAS):.1f}, real {sum(b for _, b in BIAS) / len(BIAS):.1f} ({len(BIAS)} games)')
    print('Average points per team-week (all rosters):')
    allr = [v for wk in results.values() for v in wk.values()]
    for k, name in cols:
        print(f'  {name:15} {sum(v[k] for v in allr) / len(allr):6.1f}')
    n = len(allr)
    for a, b in (('plan', 'old'),):
        diffs = [v[a] - v[b] for v in allr]
        print(f'  {a} - {b}: {sum(diffs) / n:+.1f} per week, better in {sum(x > 0 for x in diffs)} of {n}, worse in {sum(x < 0 for x in diffs)}')
    print('\nEnd of week, per team-week: slots locked (avg score) / on latest game (avg score) / empty')
    for k, name in cols[:3]:
        e = ENDS[k]
        print(f"  {name:15} {e['locked'] / e['weeks']:.1f} ({e['locked_pts'] / max(e['locked'], 1):.1f}) / "
              f"{e['latest'] / e['weeks']:.1f} ({e['latest_pts'] / max(e['latest'], 1):.1f}) / {e['empty'] / e['weeks']:.1f}")
    print('\nBy week (all rosters, average):')
    for wk, rs in sorted(results.items()):
        print(f'  week {wk}: ' + ', '.join(f'{name} {sum(v[k] for v in rs.values()) / len(rs):.1f}' for k, name in cols))
    print('\nYour team:')
    for wk, rs in sorted(results.items()):
        v = rs[me]
        print(f'  week {wk}: ' + ', '.join(f'{name} {v[k]:.1f}' for k, name in cols))


if __name__ == '__main__':
    main()
