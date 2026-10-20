"""Download what the daily run needs from Sleeper. Saves raw JSON under data/season/raw/."""
import json, os, time, urllib.request

LEAGUE = '1411220743328387072'
ME = '872689087335858176'  # Sleeper user_id of alemtani
SEASON = '2026'
RAW = 'data/season/raw'


def get(url, path=None, refresh=True):
    path = path and os.path.join(RAW, path)
    if path and os.path.exists(path) and not refresh:
        return json.load(open(path))
    if refresh:  # Sleeper's server can return an old copy; ask for a fresh one
        url += ('&' if '?' in url else '?') + f't={int(time.time())}'
    req = urllib.request.Request(url, headers={'User-Agent': 'lockin-season', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)
    return json.loads(data)


def league_data(week):
    v1 = 'https://api.sleeper.app/v1'
    return dict(
        league=get(f'{v1}/league/{LEAGUE}', 'league.json'),
        rosters=get(f'{v1}/league/{LEAGUE}/rosters', 'rosters.json'),
        users=get(f'{v1}/league/{LEAGUE}/users', 'users.json'),
        matchups=get(f'{v1}/league/{LEAGUE}/matchups/{week}', f'matchups_{week}.json'),
    )


def state():
    return get('https://api.sleeper.app/v1/state/nba', 'state.json')


def players():
    return get('https://api.sleeper.app/v1/players/nba', 'players.json')


def schedule():
    return get(f'https://api.sleeper.com/schedule/nba/regular/{SEASON}', 'schedule.json')


def season_projection():
    """Per-game season projections (RotoWire via Sleeper). Same file build_seed.py uses."""
    if os.path.exists('data/sleeper_proj.json'):
        return json.load(open('data/sleeper_proj.json'))
    return get(f'https://api.sleeper.com/projections/nba/{SEASON}?season_type=regular', 'sleeper_proj.json')


def week_stats(week, final=False):
    """Game-by-game stat lines for one week. A finished week never changes, so keep it."""
    return get(f'https://api.sleeper.com/stats/nba/{SEASON}/{week}?season_type=regular',
               f'stats_week_{week}.json', refresh=not final)


def week_projection(week):
    """Game-by-game projections for one week (RotoWire via Sleeper)."""
    return get(f'https://api.sleeper.com/projections/nba/{SEASON}/{week}?season_type=regular', f'proj_week_{week}.json')
