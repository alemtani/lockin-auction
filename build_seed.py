"""Build seed_values.csv: add Sleeper IDs, blend ESPN projections, recompute values.

Run by hand before the draft:  python3 build_seed.py
Inputs (downloaded with --fetch, or already in data/):
  data/seed_orig.csv       original RotoWire-only seed
  data/players_nba.json    Sleeper player directory
  data/sleeper_proj.json   Sleeper (RotoWire) 2026-27 per-game projections
  data/espn.json           ESPN 2026-27 projections (kona_player_info)
"""
import csv, json, math, re, sys, unicodedata, urllib.request

ESPN_URL = ('https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/2027'
            '/segments/0/leaguedefaults/1?view=kona_player_info')
ESPN_FILTER = '{"players":{"limit":500,"sortDraftRanks":{"sortPriority":100,"sortAsc":true,"value":"STANDARD"}}}'

# League scoring without fouls/techs/flagrants (those come from the seed, see foul_part).
SCORE = dict(pts=2, ast=1.5, stl=2, blk=1.5, fgm=.75, fga=-1.5, ftmi=-.75, dreb=.5, oreb=1.5, to=-2)
# ESPN stat IDs (checked against Jokic 2025-26 actuals).
ESPN_ID = dict(pts='0', blk='1', stl='2', ast='3', oreb='4', dreb='5', reb='6', to='11',
               fgm='13', fga='14', ftm='15', fta='16')
C = [0, 0, 0.399, 0.629, 0.789]


def fetch():
    def get(url, path, headers={}):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
            f.write(r.read())
    get('https://api.sleeper.app/v1/players/nba', 'data/players_nba.json')
    get('https://api.sleeper.com/projections/nba/2026?season_type=regular', 'data/sleeper_proj.json')
    get(ESPN_URL, 'data/espn.json', {'X-Fantasy-Filter': ESPN_FILTER})


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\b(jr|sr|ii|iii|iv)\b', '', s)
    return re.sub(r'[^a-z]', '', s)


def core_fp(st):
    st = dict(st)
    st.setdefault('ftmi', st.get('fta', 0) - st.get('ftm', 0))
    if 'oreb' not in st:
        st['oreb'] = st.get('reb', 0) - st.get('dreb', 0)
    return sum(st.get(k, 0) * v for k, v in SCORE.items())


def weekly(mu, sigma, a):
    tot = 0
    for n in (3, 4):
        tot += 0.5 * sum(math.comb(n, k) * a**k * (1 - a)**(n - k) * (mu + C[k] * sigma)
                         for k in range(1, n + 1))
    return tot


def espn_stats(entry):
    """Per-game ESPN 2026-27 projection in Sleeper stat names, or None."""
    by_id = {s.get('id'): s.get('averageStats') or {} for s in entry['player'].get('stats', [])}
    proj, last = by_id.get('102027'), by_id.get('002026') or {}
    if not proj:
        return None
    st = {k: proj.get(i, 0) for k, i in ESPN_ID.items()}
    # ESPN projects total rebounds only; split by last season's ratio when known.
    reb, lreb = st['reb'], last.get('6', 0)
    st['oreb'] = reb * (last.get('4', 0) / lreb) if lreb else None
    st['dreb'] = reb - st['oreb'] if st['oreb'] is not None else None
    return st


def espn_gp(entry):
    """ESPN's projected games played for 2026-27, or ''."""
    for s in entry['player'].get('stats', []):
        if s.get('id') == '102027' and (s.get('averageStats') or {}).get('42'):
            return round(s['averageStats']['42'])
    return ''


def main():
    if '--fetch' in sys.argv:
        fetch()
    seed = list(csv.DictReader(open('data/seed_orig.csv'), delimiter='|'))
    players = json.load(open('data/players_nba.json'))
    rw = {r['player_id']: r['stats'] for r in json.load(open('data/sleeper_proj.json'))}
    espn = {}
    for e in json.load(open('data/espn.json'))['players']:
        espn.setdefault(norm(e['player']['fullName']), e)

    byname = {}
    for pid, p in players.items():
        if p.get('first_name'):
            byname.setdefault(norm(p['first_name'] + p['last_name']), []).append(pid)

    out, no_espn = [], []
    for r in seed:
        ids = byname.get(norm(r['name']), [])
        if len(ids) > 1:
            ids = [i for i in ids if players[i].get('team') == r['team']] or [i for i in ids if i in rw]
        if len(ids) != 1:
            sys.exit(f"cannot match {r['name']}: {ids}")
        sid, p = ids[0], players[ids[0]]
        seed_fpg = float(r['fpg'])
        rw_st = rw.get(sid)
        rw_core = core_fp(rw_st) if rw_st else seed_fpg
        foul_part = seed_fpg - rw_core  # PF/tech/flagrant per game the seed already applied

        e = espn.get(norm(r['name']))
        es = espn_stats(e) if e else None
        if es and es['oreb'] is None and rw_st and rw_st.get('reb'):
            ratio = rw_st.get('dreb', 0) / rw_st['reb']
            es['dreb'], es['oreb'] = es['reb'] * ratio, es['reb'] * (1 - ratio)
        if es and es['oreb'] is None:
            es['oreb'], es['dreb'] = es['reb'] * .25, es['reb'] * .75
        espn_fpg = core_fp(es) + foul_part if es else None
        if espn_fpg is None:
            no_espn.append(r['name'])
        fpg = (seed_fpg + espn_fpg) / 2 if espn_fpg is not None else seed_fpg

        # Rescale sigma with the projection; use the fit if it moved more than x1.5.
        ratio = fpg / seed_fpg if seed_fpg else 1
        sigma = float(r['sigma']) * ratio if 1 / 1.5 <= ratio <= 1.5 else 8.5 + 0.19 * fpg
        a = min(0.98, int(r['proj_gp']) / 82)

        flags = [f for f in r['flags'].split('; ') if f and not f.startswith('Sleeper status')]
        if p.get('injury_status'):
            flags.append(f"Sleeper status: {p['injury_status']}")
        if espn_fpg is None:
            flags.append('no ESPN projection')
        out.append(dict(
            name=r['name'], pos='/'.join(p.get('fantasy_positions') or [r['pos']]) if not r['pos'] else r['pos'],
            team=p.get('team') or r['team'], fpg=round(fpg, 1), rw_fpg=round(seed_fpg, 1),
            espn_fpg='' if espn_fpg is None else round(espn_fpg, 1),
            last_fpg=r['last_fpg'], last_gp=r['last_gp'], sigma=round(sigma, 1), proj_gp=r['proj_gp'],
            weekly=round(weekly(fpg, sigma, a), 1), adp_rank=r['adp_rank'], sleeper_id=sid,
            years_exp=p.get('years_exp', ''), injury=p.get('injury_status') or '', flags='; '.join(flags),
            espn_gp=espn_gp(e) if e else ''))

    out.sort(key=lambda x: -x['weekly'])
    cols = ['rank', 'name', 'pos', 'team', 'fpg', 'rw_fpg', 'espn_fpg', 'last_fpg', 'last_gp', 'sigma',
            'proj_gp', 'weekly', 'adp_rank', 'sleeper_id', 'years_exp', 'injury', 'flags', 'espn_gp']
    with open('seed_values.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, cols, delimiter='|', lineterminator='\n')
        w.writeheader()
        for i, x in enumerate(out, 1):
            w.writerow(dict(x, rank=i))

    # Checks: $ total and the Jokic/Wemby inflation test row.
    rep = out[99]['weekly']
    vorp = [max(0, x['weekly'] - rep) for x in out]
    tot = sum(vorp[:140])
    val = [1 + v / tot * 1510 if i < 140 else 0 for i, v in enumerate(vorp)]
    j = next(i for i, x in enumerate(out) if x['name'].startswith('Nikola Jok'))
    wb = next(i for i, x in enumerate(out) if 'Wembanyama' in x['name'])
    infl = (1520 - 138) / (1510 - (val[j] - 1) - (val[wb] - 1))
    print(f'players {len(out)}, ESPN matched {len(out) - len(no_espn)}, missing: {no_espn}')
    print(f'$ total {sum(val):.1f} (want 1650 ±1); inflation after Jokic $60 + Wemby $70: {infl:.3f} (want ≈0.99)')


if __name__ == '__main__':
    main()
