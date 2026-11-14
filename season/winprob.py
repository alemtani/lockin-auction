"""Win-chance check on the morning calls. Shadow mode: it reports, it never changes a call.

The planner picks the option with the most expected points. Late in a week you lead, a sure score can be
worth more than a slightly higher average with a bigger swing. This module scores the same options by the
chance to win the week instead, and reports where the two disagree.

The math is the planner's lock math (model.LockIn) with one change: the final score x is worth u(x), not x.
  u(x) = x                        -> expected points (what the planner uses)
  u(x) = P(rest + x > 0)          -> win chance, rest ~ Normal(m, s) = your other slots minus their team
Early in the week s is large, so u is nearly a straight line and both versions agree. The swing only
counts late, when s is small. s is padded for what the model can't see (PAD_WEEK, and the opponent's
locks, which Sleeper doesn't share), so a guess that the week is settled can't make it play safe too early.

Pure functions. No network, no files.
"""
import math, random
import model

MODE_OFF, MODE_SHADOW = 'off', 'shadow'
GAIN_MIN = 0.01   # win chance must rise by at least 1 point to disagree with the planner
COST_MAX = 2.0    # and give up at most this many expected points
PAD_WEEK = 20.0   # points: model error over a full week of games, scaled by the share of game days left
OPP_UNLOCKED = 0.1  # chance an opponent's assumed lock wasn't made (Sleeper doesn't share locks); then his next game
                    # replaces the score. Moves their expected total and adds swing.


def phi(x):
    return model._phi_cdf(x)


class Slot:
    """Best expected u(final score) for one slot. games: [(mu, chance he plays, sigma)] in date order.

    Each morning before a game you hold an unlocked score x (or nothing). Lock it (u(x)), or play the game:
    if he plays, the new score replaces x and you choose again; if he sits, x stays. With nothing held you
    can't lock, and a slot that never gets a game ends at 0.
    """

    def __init__(self, games, u):
        self.g, self.u, n = list(games), u, len(games)
        self.c = [0.0] * n  # c[i] = expected value just after game i is played, before you see the score
        for i in range(n - 1, -1, -1):
            mu, _, sd = self.g[i]
            self.c[i] = sum(w * self.hold(mu + sd * z, i + 1) for z, w in zip(model._Z, model._W))

    def go_on(self, x, i):
        """Value of playing game i while holding x (x=None: nothing held)."""
        a = self.g[i][1]
        return a * self.c[i] + (1 - a) * self.hold(x, i + 1)

    def hold(self, x, i):
        """Best value holding x before game i (x=None: nothing held, so no lock)."""
        if i >= len(self.g):
            return self.u(0.0 if x is None else x)
        v = self.go_on(x, i)
        return v if x is None else max(self.u(x), v)

    def value(self, current=None, roll=False):
        """Expected u of the final score. roll: today's game must be played (no lock this morning)."""
        if not self.g:
            return self.u(0.0 if current is None else current)
        return self.go_on(current, 0) if roll else self.hold(current, 0)

    def simulate(self, current=None, roll=False, n=4000, seed=1):
        """Final scores when every later choice follows this u."""
        rng, out = random.Random(seed), []
        for _ in range(n):
            x = current
            for i, (mu, a, sd) in enumerate(self.g):
                if x is not None and not (roll and i == 0) and self.u(x) >= self.go_on(x, i) - 1e-12:
                    break  # lock
                if rng.random() < a:
                    x = rng.gauss(mu, sd)
            out.append(0.0 if x is None else x)
        return out


def mean_var(xs):
    m = sum(xs) / len(xs)
    return m, sum((x - m) ** 2 for x in xs) / len(xs)


def ev(x):
    return x


def win_u(m, s):
    return lambda x: phi((m + x) / s)


def option(name, games, current, roll, m, s):
    """One option for the slot, scored both ways. Its expected points follow the win-chance choices."""
    if games is None:  # lock now: the score is final
        return dict(name=name, ev=current, wp=phi((m + current) / s), ev_wp=current)
    E, W = Slot(games, ev), Slot(games, win_u(m, s))
    xs = W.simulate(current, roll)
    return dict(name=name, ev=E.value(current, roll), wp=W.value(current, roll), ev_wp=sum(xs) / len(xs))


def compare(opts, planner_pick):
    """opts: two options from option(). planner_pick: the name the planner chose.

    Returns dict(ev_pick, wp_pick, cost, gain, disagree, match). match is False when this simplified view
    doesn't reproduce the planner's choice (its slot plan sees more players); those are not reported.
    disagree: win chance picks the other option, gains GAIN_MIN or more, and costs COST_MAX or less.
    """
    e = max(opts, key=lambda o: o['ev'])
    w = max(opts, key=lambda o: o['wp'])
    other = next(o for o in opts if o is not w)
    gain = w['wp'] - other['wp']
    cost = e['ev'] - w['ev_wp']
    match = e['name'] == planner_pick
    return dict(ev_pick=e['name'], wp_pick=w['name'], gain=gain, cost=cost, match=match,
                disagree=match and w['name'] != e['name'] and gain >= GAIN_MIN and cost <= COST_MAX)
