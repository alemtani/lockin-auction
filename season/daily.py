"""Daily run: fetch → update every player → decide → email.

  python3 season/daily.py --dry-run              print today's email, send nothing
  python3 season/daily.py --date 2026-10-26      replay a day (uses the data as of now)
  python3 season/daily.py --send                 send with Gmail SMTP (needs GMAIL_APP_PASSWORD)

Monday mode (the week's first game day, or earlier): lineup, matchup, waivers.
Daily mode (other days): yesterday's games, then the same.
Every morning the slot planner (planner.py) makes the lock calls and picks today's lineup.
Waiver pickups: every day. Mid-week a move needs twice the Monday gain.
"""
import argparse, csv, datetime, os, sys, zoneinfo
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import fetch, lineup, model, planner, report

OUT = 'data/season'
LOCKS = f'{OUT}/locks.txt'
LDIR = f'{OUT}/planner_locks'  # the planner's lock calls, one file per morning: pid|slot|score|game date
PLAN_START = '2026-11-09'      # the first morning the planner made the calls; earlier games use calls/<game date>.txt
TO = 'alex.m.tanimoto@gmail.com'
FA_POOL = 25        # free agents to test as pickups
DROP_POOL = 5       # my lowest-value players to test as drops
GAIN_FA = 2.0       # points per week: free agent, no claim needed
GAIN_CLAIM = 4.0    # points per week: player on waivers, costs priority
NEXT_WEEK = 0.5     # weight of next week's value in waiver math
TIE = 1.5           # points: a close call, for lineups and locks alike. Favorite plays it safe, underdog takes the risk.
RISE_MIN = 1.25     # recent minutes this far above his projected minutes = a bigger role
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


def load_games(week, day, scoring, save=True):
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
    if not save:  # a replay: games.csv keeps every game, for the backtest
        return games
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
    games = load_games(week, day, scoring, save=not args.date)
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
        avail = [model.game_avail(b['a'], b['injury'], sl_proj.get((pid, d)), (pid, d) in sl_sits, i == 0, b['a_season'])
                 for i, d in enumerate(dates)]
        return model.LockIn(b['mu'], b['sigma'], b['a'], len(dates), mus, avail)

    def avail_left(pid):
        dates = left.get(B[pid]['team'], [])
        return lock(pid, dates).avail if dates else []

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
    R = dict(day=day, week=week, mode=mode, week_dates=weeks[week], me=users.get(fetch.ME, 'me'), tie=TIE)

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

    def alive(pid, date):
        """False if he was benched on a later day: that drops his score."""
        d = datetime.date.fromisoformat(date) + datetime.timedelta(days=1)
        while d.isoformat() <= yday:
            f = f'{sdir}/{d.isoformat()}.txt'
            if os.path.exists(f) and pid not in open(f).read().split():
                return False
            d += datetime.timedelta(days=1)
        return True

    # Lock state. Sleeper's public data does not show locks, so assume you followed every call.
    # From PLAN_START the planner makes the calls each morning (planner_locks/<day>.txt). Earlier games use
    # the old per-game calls (calls/<game date>.txt). locks.txt, your own record, wins over both.
    cdir = f'{OUT}/calls'
    calls_on = {}
    by_name = {B[pid]['name'].lower(): pid for pid in my_ids}
    override = {}
    if os.path.exists(LOCKS):
        for l in open(LOCKS).read().splitlines():
            parts = [x.strip() for x in l.split('#')[0].split('|')]
            if len(parts) == 3 and parts[1].lower() in by_name and parts[2].lower() in ('lock', 'roll'):
                override[parts[0], by_name[parts[1].lower()]] = parts[2].lower() == 'lock'
            elif parts != ['']:
                print(f'locks.txt: skipped line: {l}', file=sys.stderr)
    auto = set()  # (pid, game date) the planner locked on an earlier morning
    if os.path.isdir(LDIR):
        for fn in sorted(os.listdir(LDIR)):
            if fn[:-4] < day:
                auto |= {tuple(l.split('|')[0::3][:2]) for l in open(f'{LDIR}/{fn}').read().splitlines() if l}

    def stored_call(pid, date):
        if date not in calls_on:
            f = f'{cdir}/{date}.txt'
            calls_on[date] = dict(l.split('|')[:2] for l in open(f).read().splitlines() if l) if os.path.exists(f) else {}
        return calls_on[date].get(pid)

    def lock_call(pid, date, fp):
        """The old per-player call after a game. Used for the opponent and for games before PLAN_START."""
        after = [d for d in this_week.get(B[pid]['team'], []) if d > date]
        if not after:
            return 'FINAL', None
        thr = lock(pid, [date] + after).threshold(len(after))
        return ('CLOSE CALL' if abs(fp - thr) < TIE else 'LOCK' if fp >= thr else 'KEEP ROLLING'), thr

    favorite = {'me': True}  # set from the win chance below

    def locked_on(pid, mine=True):
        def f(date, fp):
            if mine and (date, pid) in override:
                return override[date, pid]
            if mine and (pid, date) in auto:
                return True
            if mine and date >= PLAN_START:
                return False
            c = (stored_call(pid, date) if mine else None) or lock_call(pid, date, fp)[0]
            c = c.replace('COIN FLIP', 'CLOSE CALL')  # older saved calls use the old name
            return c in ('LOCK', 'CLOSE CALL: LOCK') or c == 'CLOSE CALL' and favorite['me'] == mine
        return f

    def this_week_games(pid):
        return [g for g in games.get(pid, []) if g['date'] in weeks[week]]

    # Per player: this week's started games so far, current score, locked or not.
    def my_week_games():
        return {pid: model.week_state(this_week_games(pid), lambda d, pid=pid: in_lineup(pid, d), locked_on(pid),
                                      lambda d, pid=pid: alive(pid, d)) for pid in my_ids}
    week_games = my_week_games()

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

    # ---------- matchup (expected scores from each player's own value) ----------
    var = lambda ps: sum(spread(p) for p in ps.values())

    def play_matchup():
        tot, picks = lineup.best_lineup(lineup_rows(my_ids, value_now))
        if opp:
            # Their games so far count too. We can't see their past lineups or locks: use today's starters
            # and assume they lock like the old per-player calls.
            o_start = set(opp.get('starters') or [])
            oval = {}
            for pid in opp['players'] or []:
                if pid in B and pid not in (opp.get('reserve') or []):
                    wg = model.week_state(this_week_games(pid), lambda d, pid=pid: pid in o_start, locked_on(pid, mine=False))
                    oval[pid] = e_week(pid, current=wg['current'], locked=wg['locked'])
            otot, opicks = lineup.best_lineup([(pid, B[pid]['pos'], v) for pid, v in oval.items()])
            R['matchup'] = dict(opp=users.get(opp['owner_id'], 'opponent'), me=tot, them=otot,
                                chance=model.win_chance(tot, var(picks), otot, var(opicks)),
                                their_top=sorted(((B[p]['name'], oval[p]) for p in opicks.values()), key=lambda x: -x[1])[:3])

    play_matchup()
    if opp and R['matchup']['chance'] < 0.5:  # underdog: close calls take the risk, on both sides
        favorite['me'] = False
        play_matchup()
    fav = favorite['me']
    R['fav'] = fav if opp else None

    # ---------- the slot planner: this morning's lock calls and today's lineup ----------
    dates = [d for d in weeks[week] if d >= day]
    pv = {}
    for pid in my_ids:
        if pid in reserve:
            continue
        ds = left.get(B[pid]['team'], [])
        Lk = lock(pid, ds) if ds else None
        pv[pid] = dict(name=B[pid]['name'], pos=B[pid]['pos'],
                       games={d: (Lk.mus[i], Lk.avail[i], B[pid]['sigma']) for i, d in enumerate(ds)} if ds else {})
    holder = {i: p for i, p in enumerate(starters_now) if p in pv}
    score, locked = {}, set()
    for i, p in holder.items():
        wg = week_games.get(p) or {}
        if wg.get('current') is not None:
            score[p] = wg['current']
        if wg.get('locked'):
            locked.add(i)
    # A locked player cannot leave his slot. If our record says locked but he is not starting, the record is wrong.
    R['lock_mismatch'] = [B[p]['name'] for p in my_ids if (week_games.get(p) or {}).get('locked') and p not in holder.values()]
    was_locked, held = set(locked), dict(score)
    game_date = {p: week_games[p].get('current_date') for p in score}
    if day in dates:
        new, notes, calls = planner.morning(day, dates, holder, score, locked, pv, tie=TIE, safe=fav)
    else:  # no games today: nothing to decide
        new, notes, calls = dict(holder), {}, []
    os.makedirs(LDIR, exist_ok=True)
    if not args.date and not os.path.exists(f'{LDIR}/{day}.txt'):  # first run of the day wins; a replay never writes
        open(f'{LDIR}/{day}.txt', 'w').write(''.join(f"{c['p']}|{lineup.SLOTS[c['slot']]}|{c['x']:.2f}|{game_date[c['p']]}\n"
                                                     for c in calls if c['kind'] == 'lock'))

    def nm(p):
        return B[p]['name'] if p in B else None

    def dn(d):
        return datetime.date.fromisoformat(d).strftime('%a')

    R['calls'] = []
    for c in calls:
        nxt_game = next((d for d in dates if d > day and d in pv[c['p']]['games']), None)
        R['calls'].append(dict(c, name=nm(c['p']), slot=lineup.SLOTS[c['slot']] if c['slot'] is not None else None,
                               from_slot=lineup.SLOTS[c['from_slot']] if c.get('from_slot') is not None else None,
                               next_game=dn(nxt_game) if nxt_game else None))
    R['plan'] = []
    later = dates[1:]
    for i, slot in enumerate(lineup.SLOTS):
        p = new.get(i)
        r = dict(slot=slot, name=nm(p) or '(empty)', injury=B[p]['injury'] if p else '', avail=avail_left(p) if p else [],
                 today='', bar='', if_below='')
        if i in locked:
            r['today'] = f"locked {score.get(p, 0):.1f}" + ('' if i in was_locked else ' (lock now)')
        elif not p or day not in pv[p]['games']:
            r['today'] = f'holds {score[p]:.1f}, no game today' if p in score else 'no game today'
        else:
            r['today'] = 'plays today' + (f' (replaces his {score[p]:.1f})' if p in score else ' (free try)')
            opts = planner.lock_options(p, i, later, new, score, locked, pv) if later else []
            if opts:
                bar, t = opts[0]
                r['bar'] = f'{bar:.0f}' + (f' (at {lineup.SLOTS[t]})' if t != i else '')
                nx = next((g for g in notes.get(i, {}).get('plan', []) if g[1] > day), None)
                r['if_below'] = ('nobody else: his score stays' if not nx else f'roll into his next game ({dn(nx[1])})' if nx[0] == p
                                 else f'bench him, try {nm(nx[0])} ({dn(nx[1])})')
            else:
                r['bar'] = 'last game: it stays'
        R['plan'].append(r)
    R['bench'] = []
    for p in my_ids:
        if p in new.values():
            continue
        g = len(left.get(B[p]['team'], []))
        today = 'IR' if p in reserve else f'drops his {held[p]:.1f}' if p in held else 'plays today' if day in (pv.get(p) or {}).get('games', {}) else f"{g} game{'' if g == 1 else 's'} left"
        R['bench'].append(dict(slot='IR' if p in reserve else 'BN', name=nm(p), today=today, injury=B[p]['injury'], avail=avail_left(p)))
    R['moves'] = []
    for i, slot in enumerate(lineup.SLOTS):
        want, now = new.get(i), starters_now[i] if i < len(starters_now) else None
        if want != now and not (want is None and now in (None, '0')):
            R['moves'].append(f"{slot}: {nm(want) or 'empty'} (now {nm(now) or 'empty'})")
    R['empty'] = [lineup.SLOTS[i] for i, a in enumerate(starters_now) if a == '0']
    open_ = [i for i in range(len(lineup.SLOTS)) if i not in locked]
    st = planner.streams({q: v for q, v in pv.items() if q not in {new[i] for i in locked if i in new}}, dates, open_)
    R['week_plan'] = [dict(slot=lineup.SLOTS[i], worth=planner.fresh_value(st[i]),
                           tries=[f"{dn(g[1])} {B[g[0]]['name'].split()[-1]}" for g in st[i]]) for i in open_]

    # ---------- player cards (model view) ----------
    def card(pid):
        b = B[pid]
        last = [g['fp'] for g in games.get(pid, [])[-3:]]
        return dict(last3=sum(last) / len(last) if last else None, name=b['name'], team=b['team'], pos='/'.join(b['pos']), prior=b['prior'], mu=b['mu'], avg=b['avg'],
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
    def role_up(pid):
        pm = (priors.get(pid) or {}).get('minutes')
        return bool(B[pid]['n'] >= 3 and B[pid]['recent_min'] and pm and B[pid]['recent_min'] >= RISE_MIN * pm)

    def rising(pid):
        """Beating his projection, or a bigger role. The edge: real games say more than the experts do."""
        b = B[pid]
        return b['n'] >= 3 and (b['avg'] >= b['prior'] or role_up(pid))

    fa = [pid for pid in B if pid not in rostered and B[pid]['injury'] not in ('Out', 'IR', 'Sus') and rising(pid)]
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
                          role_up=role_up(add)))
    moves.sort(key=lambda m: -m['gain'])
    R['waivers'] = moves[:3]

    # ---------- daily: yesterday's games ----------
    if mode == 'daily':
        R['yesterday'] = yday
        callby = {c['p']: c for c in calls}
        label = {'lock': 'LOCK', 'roll': 'ROLL', 'keep': 'WAIT', 'drop': 'DROP'}
        res = []
        for pid in my_ids:
            g = next((g for g in games.get(pid, []) if g['date'] == yday), None)
            if not g:
                continue
            started, wg = in_lineup(pid, yday), week_games[pid]
            if not started:
                call = 'bench'
            elif wg['locked'] and wg['locked_date'] < yday:
                call = 'LOCKED'
            elif wg['locked'] and wg['locked_date'] == yday:  # an old-style call or locks.txt locked last night's game
                call = 'LOCK'
            elif pid in callby:
                c = callby[pid]
                call = label[c['kind']] + (f" at {lineup.SLOTS[c['slot']]}" if c['kind'] == 'lock' else '')
            elif not left.get(B[pid]['team']):
                call = 'FINAL'
            else:
                call = 'WAIT'
            res.append(dict(pid=pid, name=B[pid]['name'], fp=g['fp'], mu=B[pid]['mu'], min=g['min'], call=call, started=started))
        res.sort(key=lambda x: (not x['started'], -x['fp']))
        R['results'] = res

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
