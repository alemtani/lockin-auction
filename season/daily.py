"""Daily run: fetch → update every player → decide → email.

  python3 season/daily.py --dry-run              print today's email, send nothing
  python3 season/daily.py --date 2026-10-26      replay a day (uses the data as of now)
  python3 season/daily.py --send                 send with Gmail SMTP (needs GMAIL_APP_PASSWORD)

Monday mode (the week's first game day, or earlier): lineup, matchup, waivers.
Daily mode (other days): yesterday's games, lock calls, bench swaps.
Waiver pickups: every day. Mid-week a move needs twice the Monday gain.
"""
import argparse, csv, datetime, os, sys, zoneinfo
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import fetch, lineup, model, report

OUT = 'data/season'
TO = 'alex.m.tanimoto@gmail.com'
FA_POOL = 25        # free agents to test as pickups
DROP_POOL = 5       # my lowest-value players to test as drops
GAIN_FA = 2.0       # points per week: free agent, no claim needed
GAIN_CLAIM = 4.0    # points per week: player on waivers, costs priority
GAIN_SWAP = 3.0     # points: bench swap
NEXT_WEEK = 0.5     # weight of next week's value in waiver math
COIN_FLIP = 2.0     # points: a starter this close to his bench alternative gets an asterisk
LOCK_TIE = 1.0      # points: a score this close to his typical best is a coin flip
MIX = 0.5           # weight of Sleeper's game-by-game projection in this week's expected points


def today_pt():
    return datetime.datetime.now(zoneinfo.ZoneInfo('America/Los_Angeles')).date().isoformat()


def week_dates(sched):
    w = defaultdict(set)
    for g in sched:
        w[g['week']].add(g['date'])
    return {k: sorted(v) for k, v in w.items()}


def current_week(weeks, day):
    for k in sorted(weeks):
        if day <= weeks[k][-1]:
            return k
    return max(weeks)


def team_games(sched, week=None, after=None, before=None, finished=False):
    """team -> list of game dates, filtered."""
    out = defaultdict(list)
    for g in sched:
        if finished and g.get('status') == 'pre_game':
            continue
        if week is not None and g['week'] != week:
            continue
        if after and g['date'] < after or before and g['date'] >= before:
            continue
        for side in ('home', 'away'):
            out[g[side]['team']].append(g['date'])
    for v in out.values():
        v.sort()
    return out


def load_games(week, day, scoring):
    """All games played before `day`: pid -> [{'date','fp','min','team'}], oldest first."""
    games = defaultdict(list)
    for w in range(1, week + 1):
        try:
            rows = fetch.week_stats(w, final=w < week)
        except Exception as e:
            print(f'stats week {w}: {e}', file=sys.stderr)
            continue
        for r in rows:
            st = r.get('stats') or {}
            if not r.get('date') or r['date'] >= day or st.get('sp', 0) <= 0:
                continue
            games[r['player_id']].append(dict(date=r['date'], fp=model.fantasy_points(st, scoring),
                                              min=st['sp'] / 60, team=r.get('team')))
    for g in games.values():
        g.sort(key=lambda x: x['date'])
    os.makedirs(OUT, exist_ok=True)
    with open(f'{OUT}/games.csv', 'w', newline='') as f:
        w = csv.writer(f, delimiter='|', lineterminator='\n')
        w.writerow(['date', 'sleeper_id', 'team', 'min', 'fp'])
        for pid, gs in sorted(games.items()):
            for g in gs:
                w.writerow([g['date'], pid, g['team'], round(g['min'], 1), round(g['fp'], 2)])
    return games


def build_priors(players, scoring):
    proj = {r['player_id']: r['stats'] for r in fetch.season_projection() if r.get('stats')}
    seed = {r['sleeper_id']: r for r in csv.DictReader(open('seed_values.csv'), delimiter='|')}
    pri = {}
    for pid, st in proj.items():
        if pid in players and st.get('sp'):
            pri[pid] = model.prior_from_proj(st, scoring)
    for pid, r in seed.items():
        pri[pid] = model.prior_from_seed(r, proj.get(pid, {}).get('sp', 0) / 60 or None)
    return pri


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--mode', choices=['monday', 'daily'])
    args = ap.parse_args()
    day = args.date or today_pt()

    sched = fetch.schedule()
    weeks = week_dates(sched)
    week = current_week(weeks, day)
    L = fetch.league_data(week)
    scoring = {k: v for k, v in L['league']['scoring_settings'].items() if v}
    players = fetch.players()
    games = load_games(week, day, scoring)
    priors = build_priors(players, scoring)
    played = team_games(sched, before=day, finished=True)

    # Every player: belief now.
    B = {}
    for pid in set(priors) | set(games):
        p = players.get(pid) or {}
        if not p.get('team'):
            continue
        pr = priors.get(pid) or model.prior_unknown()
        B[pid] = model.belief(pr, games.get(pid, []), len(played.get(p['team'], [])), p.get('injury_status') or '')
        B[pid].update(name=p.get('full_name') or pid, pos=p.get('fantasy_positions') or [], team=p['team'])

    # This week's and next week's schedule from today on.
    left = team_games(sched, week=week, after=day)
    nxt = team_games(sched, week=week + 1)
    this_week = team_games(sched, week=week)

    # Sleeper's game-by-game projections, scored with league settings: (pid, date) -> points.
    sl_proj, sl_sits = {}, set()
    for w in (week, week + 1):
        try:
            rows = fetch.week_projection(w)
        except Exception as e:
            print(f'projections week {w}: {e}', file=sys.stderr)
            continue
        for r in rows:
            st = r.get('stats') or {}
            if r.get('date') and not st.get('sp'):
                sl_sits.add((r['player_id'], r['date']))
            elif r.get('date'):
                sl_proj[r['player_id'], r['date']] = model.fantasy_points(st, scoring) + scoring.get('pf', 0) * model.PF_PER_MIN * st['sp'] / 60

    def lock(pid, dates):
        b = B[pid]
        mus = model.week_mus(b['mu'], [sl_proj.get((pid, d)) for d in dates], MIX)
        avail = [model.game_avail(b['a'], b['injury'], sl_proj.get((pid, d)), (pid, d) in sl_sits) for d in dates]
        return model.LockIn(b['mu'], b['sigma'], b['a'], len(dates), mus, avail)

    def week_avg(pid):
        dates = left.get(B[pid]['team'], [])
        return sum(lock(pid, dates).mus) / len(dates) if dates else B[pid]['mu']

    def e_week(pid, **kw):
        dates = left.get(B[pid]['team'], [])
        return lock(pid, dates).week_value(**kw) if dates else (kw.get('current') or 0.0)

    rosters = {r['roster_id']: r for r in L['rosters']}
    users = {u['user_id']: (u.get('metadata') or {}).get('team_name') or u['display_name'] for u in L['users']}
    mine = next(r for r in L['rosters'] if r['owner_id'] == fetch.ME)
    rostered = {pid for r in L['rosters'] for pid in (r['players'] or [])}
    reserve = set(mine.get('reserve') or [])
    my_ids = [pid for pid in mine['players'] if pid in B]

    mt = {m['roster_id']: m for m in L['matchups']}
    me_m = mt.get(mine['roster_id'], {})
    opp_m = next((m for m in L['matchups'] if m['matchup_id'] == me_m.get('matchup_id') and m['roster_id'] != mine['roster_id']), None)
    opp = rosters.get(opp_m['roster_id']) if opp_m else None

    first_day = weeks[week][0]
    mode = args.mode or ('monday' if day <= first_day or datetime.date.fromisoformat(day).weekday() == 0 else 'daily')
    R = dict(day=day, week=week, mode=mode, week_dates=weeks[week], me=users.get(fetch.ME, 'me'))

    # Who started last night: the lineup at run time (before you change it today). Kept per date,
    # because only a game played from a starting slot counts or can be locked.
    starters_now = (mine.get('starters') or [])[:len(lineup.SLOTS)]
    yday = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
    sdir = f'{OUT}/starters'
    os.makedirs(sdir, exist_ok=True)
    if not args.date and not os.path.exists(f'{sdir}/{yday}.txt'):  # first run of the day wins; a replay never writes
        open(f'{sdir}/{yday}.txt', 'w').write('\n'.join(starters_now) + '\n')
    started_on = {}

    def in_lineup(pid, date):
        if date not in started_on:
            f = f'{sdir}/{date}.txt'
            started_on[date] = set(open(f).read().split()) if os.path.exists(f) else set(starters_now)  # no record: best guess
        return pid in started_on[date]

    # Per player: this week's started games so far, Sleeper's current score, locked or not.
    pts = (me_m.get('players_points') or {})
    week_games = {pid: model.week_state([g for g in games.get(pid, []) if g['date'] in weeks[week]], pts.get(pid),
                                        lambda d, pid=pid: in_lineup(pid, d)) for pid in my_ids}

    def value_now(pid):
        wg = week_games.get(pid) or {}
        return e_week(pid, current=wg.get('current'), locked=wg.get('locked', False))

    def lineup_rows(ids, val):
        return [(pid, B[pid]['pos'], val(pid)) for pid in ids if pid not in reserve and pid in B]

    def spread(pid):
        dates = left.get(B[pid]['team'], [])
        if not dates:
            return 0.0
        xs = lock(pid, dates).simulate(1500)
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / len(xs)

    # ---------- lineup and matchup ----------
    rows = lineup_rows(my_ids, value_now)
    tot, picks = lineup.best_lineup(rows)
    if opp:
        orows = [(pid, B[pid]['pos'], e_week(pid)) for pid in (opp['players'] or []) if pid in B and pid not in (opp.get('reserve') or [])]
        otot, opicks = lineup.best_lineup(orows)
        var = lambda ps: sum(spread(p) for p in ps.values())
        chance = model.win_chance(tot, var(picks), otot, var(opicks))
        tilt = 0.15 if chance < 0.4 else -0.15 if chance > 0.6 else 0.0
        if tilt:
            tilted = [(pid, pos, v + tilt * spread(pid) ** 0.5) for pid, pos, v in rows]
            _, picks = lineup.best_lineup(tilted)
            tot = sum(value_now(p) for p in picks.values())
            chance = model.win_chance(tot, var(picks), otot, var(opicks))
        R['matchup'] = dict(opp=users.get(opp['owner_id'], 'opponent'), me=tot, them=otot, chance=chance, tilt=tilt,
                            their_top=sorted(((B[p]['name'], e_week(p)) for p in opicks.values()), key=lambda x: -x[1])[:3])

    # Coin flips: a starter whose place in the lineup is worth less than COIN_FLIP points.
    flips = {}
    for pid in picks.values():
        t2, p2 = lineup.best_lineup([r for r in rows if r[0] != pid])
        if tot - t2 < COIN_FLIP:
            other = next(iter(set(p2.values()) - set(picks.values())), None)
            flips[pid] = dict(other=B[other]['name'] if other else '?', other_v=value_now(other) if other else 0, margin=tot - t2)

    R['lineup'] = []
    for i, s in enumerate(lineup.SLOTS):
        pid = picks.get(i)
        b = B.get(pid, {})
        R['lineup'].append(dict(slot=s, pid=pid, name=b.get('name', '—'), value=value_now(pid) if pid else 0,
                                games=len(left.get(b.get('team'), [])), mu=week_avg(pid) if pid else 0, a=b.get('a', 0),
                                injury=b.get('injury', ''), flip=flips.get(pid), now=B.get(starters_now[i], {}).get('name') if i < len(starters_now) else None))
    bench = [pid for pid in my_ids if pid not in picks.values()]
    R['bench'] = [dict(name=B[p]['name'], value=value_now(p), games=len(left.get(B[p]['team'], [])), mu=week_avg(p),
                       injury=B[p]['injury'], ir=p in reserve) for p in bench]
    R['bench'].sort(key=lambda x: -x['value'])
    R['changes'] = [(B[a]['name'] if a in B else a) for a in set(starters_now) - set(picks.values()) if a != '0']
    R['empty'] = [lineup.SLOTS[i] for i, a in enumerate(starters_now) if a == '0']
    R['changes_in'] = [B[p]['name'] for p in set(picks.values()) - set(starters_now)]

    # ---------- player cards (model view) ----------
    def card(pid):
        b = B[pid]
        return dict(name=b['name'], team=b['team'], pos='/'.join(b['pos']), prior=b['prior'], mu=b['mu'], avg=b['avg'],
                    n=b['n'], trust=b['trust'], k=b['k'], minutes=b['minutes'], recent_min=b['recent_min'],
                    prior_min=(priors.get(pid) or {}).get('minutes'), a=b['a'], injury=b['injury'], sigma=b['sigma'])
    R['cards'] = {pid: card(pid) for pid in my_ids}

    # ---------- waivers ----------
    def wval(pid):
        t = B[pid]['team']
        v = e_week(pid)
        nd = nxt.get(t, [])
        return v + NEXT_WEEK * (lock(pid, nd).week_value() if nd else 0)

    tx = []
    try:
        tx = fetch.get(f'https://api.sleeper.app/v1/league/{fetch.LEAGUE}/transactions/{week}', f'transactions_{week}.json')
    except Exception:
        pass
    cutoff = (datetime.date.fromisoformat(day) - datetime.timedelta(days=2)).isoformat()
    on_waivers = {pid for t in tx if t.get('drops') and datetime.datetime.fromtimestamp(t['created'] / 1000).date().isoformat() >= cutoff
                  for pid in t['drops']}
    fa = [pid for pid in B if pid not in rostered and B[pid]['injury'] not in ('Out', 'IR', 'Sus')]
    fa_vals = sorted(((wval(p), p) for p in fa), reverse=True)[:FA_POOL]
    mine_rows = [(pid, B[pid]['pos'], wval(pid)) for pid in my_ids if pid not in reserve]
    drops = {p for _, p in sorted((v, pid) for pid, _, v in mine_rows)[:DROP_POOL]}
    base = lineup.team_value(mine_rows)[0]
    moves = []
    for v, add in fa_vals:
        best = None
        for d in drops:
            roster = [r for r in mine_rows if r[0] != d] + [(add, B[add]['pos'], v)]
            g = lineup.team_value(roster)[0] - base
            if best is None or g > best[0]:
                best = (g, d)
        claim = add in on_waivers
        need = (GAIN_CLAIM if claim else GAIN_FA) * (1 if mode == 'monday' else 2)
        moves.append(dict(gain=best[0], add=card(add), drop=B[best[1]]['name'], claim=claim, ok=best[0] >= need,
                          games=len(left.get(B[add]['team'], [])), games_next=len(nxt.get(B[add]['team'], [])),
                          role_up=B[add]['n'] >= 3 and B[add]['recent_min'] and B[add]['recent_min'] >= 1.25 * ((priors.get(add) or {}).get('minutes') or 99)))
    moves.sort(key=lambda m: -m['gain'])
    R['waivers'] = moves[:3]

    # ---------- daily: yesterday, lock calls, swaps ----------
    if mode == 'daily':
        R['yesterday'] = yday
        res = []
        for pid in my_ids:
            g = next((g for g in games.get(pid, []) if g['date'] == yday), None)
            if not g:
                continue
            b, wg = B[pid], week_games[pid]
            r = len(left.get(b['team'], []))
            lk = lock(pid, [yday] + left.get(b['team'], []))
            thr = lk.threshold(r) if r else None
            started = in_lineup(pid, yday)
            call = 'FINAL' if not r else 'LOCKED' if wg['locked'] else 'COIN FLIP' if abs(g['fp'] - thr) < LOCK_TIE else 'LOCK' if g['fp'] >= thr else 'KEEP ROLLING'
            res.append(dict(name=b['name'], fp=g['fp'], mu=b['mu'], sigma=b['sigma'], z=(g['fp'] - b['mu']) / b['sigma'],
                            min=g['min'], left=r, thr=thr, call=call if started else 'bench', started=started))
        res.sort(key=lambda x: (not x['started'], -x['fp']))
        R['results'] = res
        swaps = []
        for i, s in enumerate(lineup.SLOTS):
            if i >= len(starters_now) or starters_now[i] not in B:
                continue
            st = starters_now[i]
            sv = value_now(st)
            for bp in bench:
                if bp in reserve or not lineup.fits(B[bp]['pos'], s):
                    continue
                bv = e_week(bp)  # his games count only after he moves in
                g = lineup.swap_gain(sv, bv)
                if g >= GAIN_SWAP:
                    swaps.append(dict(slot=s, out=B[st]['name'], into=B[bp]['name'], gain=g, out_v=sv, in_v=bv,
                                      in_games=len(left.get(B[bp]['team'], []))))
        swaps.sort(key=lambda x: -x['gain'])
        seen, R['swaps'] = set(), []
        for sw in swaps:
            if sw['out'] in seen or sw['into'] in seen:
                continue
            seen |= {sw['out'], sw['into']}
            R['swaps'].append(sw)

    # ---------- history: movers vs a week ago ----------
    hist = f'{OUT}/history'
    os.makedirs(hist, exist_ok=True)
    with open(f'{hist}/{day}.csv', 'w', newline='') as f:
        w = csv.writer(f, delimiter='|', lineterminator='\n')
        w.writerow(['sleeper_id', 'name', 'team', 'prior', 'mu', 'sigma', 'a', 'n', 'trust', 'minutes'])
        for pid, b in sorted(B.items(), key=lambda x: -x[1]['mu']):
            if b['mu'] >= 12 or pid in rostered:
                w.writerow([pid, b['name'], b['team'], round(b['prior'], 1), round(b['mu'], 1), round(b['sigma'], 1),
                            round(b['a'], 2), b['n'], round(b['trust'], 2), round(b['minutes'], 1)])
    old = sorted(f for f in os.listdir(hist) if f < f'{(datetime.date.fromisoformat(day) - datetime.timedelta(days=6)).isoformat()}.csv')
    movers = []
    if old:
        prev = {r['sleeper_id']: float(r['mu']) for r in csv.DictReader(open(f'{hist}/{old[-1]}'), delimiter='|')}
        for pid in my_ids:
            if pid in prev and abs(B[pid]['mu'] - prev[pid]) >= 2:
                movers.append((B[pid]['name'], prev[pid], B[pid]['mu']))
    R['movers'] = movers

    subject, html, text = report.render(R)
    os.makedirs(f'{OUT}/out', exist_ok=True)
    open(f'{OUT}/out/{day}.html', 'w').write(html)
    open(f'{OUT}/out/{day}.txt', 'w').write(subject + '\n\n' + text)
    if args.dry_run or not args.send:
        print(subject + '\n\n' + text)
    if args.send and not os.environ.get('GMAIL_APP_PASSWORD'):
        print('GMAIL_APP_PASSWORD is not set: email not sent', file=sys.stderr)
    elif args.send:
        report.send(subject, html, text, TO)


if __name__ == '__main__':
    main()
