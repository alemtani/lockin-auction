"""Player model: start at the preseason projection, move toward real games.

Pure functions only. No network, no files.

The idea, per player:
  belief = mix of projection and this season's games.
  trust in the games = n / (n + k), with k = sigma² / tau²:
    sigma = how much his score swings game to game,
    tau   = how wrong the projection could be.
  After k games, projection and games count the same.

Points per game = minutes × points per minute.
  Points per minute is skill: it moves slowly (shrink with k).
  Minutes is role: it follows recent games fast (small K_MIN).
"""
import math, random

K_MIN = 3          # games of prior weight for minutes (role changes show up fast)
K_SIGMA = 10       # games of prior weight for the game-to-game swing
K_AVAIL = 8        # games of prior weight for "chance he plays"
HALF_LIFE = 40     # games: a game this old counts half
HALF_LIFE_MIN = 5  # games: minutes follow the last ~week
PF_PER_MIN = 0.07  # fouls per minute, for projections without fouls
INJ_AVAIL = {'Out': 0.1, 'IR': 0.0, 'Sus': 0.0, 'DTD': 0.65, 'GTD': 0.65, 'Questionable': 0.6, 'Doubtful': 0.25}


def fantasy_points(st, scoring):
    """League fantasy points for one stat line (per game or per-game projection)."""
    st = dict(st)
    if 'ftmi' not in st and 'fta' in st:
        st['ftmi'] = st['fta'] - st.get('ftm', 0)
    if 'fgmi' not in st and 'fga' in st:
        st['fgmi'] = st['fga'] - st.get('fgm', 0)
    if 'oreb' not in st and 'reb' in st:
        st['oreb'] = st['reb'] - st.get('dreb', 0)
    return sum(st.get(k, 0) * w for k, w in scoring.items())


def prior_from_seed(r, minutes):
    """Prior from a seed_values.csv row. minutes = projected minutes (None if unknown)."""
    fpg, rw = float(r['fpg']), float(r['rw_fpg'] or r['fpg'])
    espn = float(r['espn_fpg']) if r['espn_fpg'] else rw
    sigma = float(r['sigma'])
    tau = max(0.3 * sigma, abs(rw - espn))  # about 11 games to 50/50 when the sources agree
    if int(r['last_gp'] or 0) < 40:
        tau *= 1.3  # injury history: the projection is less sure
    if abs(int(r['adp_rank'] or 0) - int(r['rank'])) > 40:
        tau *= 1.3  # the draft market disagreed with our seed: less sure
    return dict(fpg=fpg, sigma=sigma, tau=tau, a=min(0.98, int(r['proj_gp']) / 82),
                minutes=minutes or max(10.0, fpg), src='seed')


def prior_from_proj(st, scoring):
    """Prior from a Sleeper per-game season projection (players not in the seed)."""
    minutes = st.get('sp', 0) / 60
    fpg = fantasy_points(st, scoring) + scoring.get('pf', 0) * PF_PER_MIN * minutes  # projections have no fouls
    return dict(fpg=fpg, sigma=8.5 + 0.19 * max(fpg, 0), tau=6.0, a=0.8, minutes=max(minutes, 1.0), src='proj')


def prior_unknown():
    """Prior for a player with no projection who starts playing."""
    return dict(fpg=10.0, sigma=10.4, tau=8.0, a=0.6, minutes=15.0, src='none')


def stabilize_games(prior):
    """k: games until real games count as much as the projection."""
    return prior['sigma'] ** 2 / prior['tau'] ** 2


def belief(prior, games, team_games=0, injury=''):
    """Current belief for one player.

    games: this season's games, oldest first, each {'fp', 'min'} (only games he played).
    team_games: games his team has played so far (for "chance he plays").
    """
    k = stabilize_games(prior)
    n = len(games)
    out = dict(prior=prior['fpg'], k=k, n=n, src=prior['src'])
    if n == 0:
        out.update(mu=prior['fpg'], sigma=prior['sigma'], trust=0.0, minutes=prior['minutes'],
                   avg=None, recent_min=None, rate=prior['fpg'] / prior['minutes'])
    else:
        w = [0.5 ** ((n - 1 - i) / HALF_LIFE) for i in range(n)]
        n_eff = sum(w)
        fp_w = sum(wi * g['fp'] for wi, g in zip(w, games))
        min_w = sum(wi * g['min'] for wi, g in zip(w, games))
        rate0 = prior['fpg'] / prior['minutes']
        obs_rate = fp_w / min_w if min_w > 0 else rate0
        rate = (k * rate0 + n_eff * obs_rate) / (k + n_eff)
        wm = [0.5 ** ((n - 1 - i) / HALF_LIFE_MIN) for i in range(n)]
        recent_min = sum(wi * g['min'] for wi, g in zip(wm, games)) / sum(wm)
        minutes = (K_MIN * prior['minutes'] + sum(wm) * recent_min) / (K_MIN + sum(wm))
        fps = [g['fp'] for g in games]
        avg = sum(fps) / n
        s2 = sum((x - avg) ** 2 for x in fps) / (n - 1) if n > 1 else prior['sigma'] ** 2
        sigma = math.sqrt((K_SIGMA * prior['sigma'] ** 2 + (n - 1) * s2) / (K_SIGMA + n - 1))
        out.update(mu=rate * minutes, sigma=sigma, trust=n_eff / (n_eff + k), minutes=minutes,
                   avg=avg, recent_min=recent_min, rate=rate)
    a = (K_AVAIL * prior['a'] + n) / (K_AVAIL + team_games) if team_games else prior['a']
    a = max(0.0, min(0.98, a))
    if injury in INJ_AVAIL:
        a = min(a, INJ_AVAIL[injury])
    out['a'] = a
    out['injury'] = injury
    return out


# ---------- Lock-In: see each game, lock the one you want; if you never lock, the latest game counts ----------

_Z = [i / 10 for i in range(-45, 46)]
_W = [math.exp(-z * z / 2) for z in _Z]
_W = [x / sum(_W) for x in _W]


class LockIn:
    """Optimal lock policy for one player for one week.

    eu[r] = expected final score if a game was just played and r more games are left,
            before you know that game's score (so you can still lock it).
    """

    def __init__(self, mu, sigma, a, games):
        self.mu, self.sigma, self.a, self.games = mu, sigma, a, games
        self.eu = [mu]
        for r in range(1, games):
            self.eu.append(sum(w * self.after_game(mu + sigma * z, r) for z, w in zip(_Z, _W)))

    def after_game(self, x, r):
        """Best expected result holding score x (unlocked) with r games left."""
        v = x
        for j in range(1, r + 1):  # build up from 1 game left to r games left
            v = max(x, self.a * self.eu[j - 1] + (1 - self.a) * v)
        return v

    def threshold(self, r):
        """After a game with r games left: lock if the score is at least this."""
        return self.eu[r - 1] if r >= 1 else -math.inf

    def week_value(self, games_left=None, current=None, locked=False):
        """Expected final score. current = his latest unlocked score this week (None if no game yet)."""
        r = self.games if games_left is None else games_left
        if locked:
            return current
        if current is not None:
            return self.after_game(current, r)
        v = 0.0  # no game yet; if he never plays, the slot scores 0
        for j in range(1, r + 1):
            v = self.a * self.eu[j - 1] + (1 - self.a) * v
        return v

    def simulate(self, n=3000, seed=1):
        """Final scores under the lock policy (for spread / win chance)."""
        rng, out = random.Random(seed), []
        for _ in range(n):
            x = None
            for g in range(self.games):
                left = self.games - g - 1
                if rng.random() < self.a:
                    x = rng.gauss(self.mu, self.sigma)
                if x is not None and left > 0 and x >= self.threshold(left):
                    break
            out.append(x if x is not None else 0.0)
        return out


def best_of_c(r):
    """How many sigmas above average you expect to end up with r games (always plays)."""
    c = 0.0
    for _ in range(r - 1):
        c = c * _phi_cdf(c) + math.exp(-c * c / 2) / math.sqrt(2 * math.pi)
    return c


def _phi_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_chance(me_mean, me_var, opp_mean, opp_var):
    sd = math.sqrt(me_var + opp_var)
    return _phi_cdf((me_mean - opp_mean) / sd) if sd > 0 else 0.5
