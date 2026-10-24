"""Run: python3 -m unittest discover season"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(__file__))
import lineup, model

SCORING = dict(pts=2, ast=1.5, stl=2, blk=1.5, fgm=.75, fga=-1.5, ftmi=-.75, dreb=.5, oreb=1.5, to=-2, pf=-.75, tf=-1.5, ff=-3)


def vet(fpg=50.0, sigma=15.5):
    return dict(fpg=fpg, sigma=sigma, tau=0.3 * sigma, a=0.95, minutes=34.0, src='seed')


def games(n, fp, minutes=34.0):
    return [dict(fp=fp, min=minutes) for _ in range(n)]


class LockInMath(unittest.TestCase):
    def test_best_of_c_matches_seed(self):
        for r, c in zip(range(1, 6), [0, 0.399, 0.629, 0.79, 0.911]):  # build_seed.py's C, plus 5 games
            self.assertAlmostEqual(model.best_of_c(r), c, delta=0.003)

    def test_lock_week_example(self):
        lk = model.LockIn(40, 12, 1.0, 4)
        self.assertAlmostEqual(lk.threshold(3), 47.5, delta=0.1)  # Mon 46 → keep rolling
        self.assertAlmostEqual(lk.threshold(2), 44.8, delta=0.1)  # Wed 49 → lock
        self.assertLess(46, lk.threshold(3))
        self.assertGreater(49, lk.threshold(2))

    def test_threshold_ignores_missed_games(self):
        # The latest game counts, so a missed game keeps the current score. Threshold = best-of-rest.
        full, half = model.LockIn(40, 12, 1.0, 4), model.LockIn(40, 12, 0.5, 4)
        self.assertLess(half.threshold(3), full.threshold(3))
        self.assertGreater(half.threshold(3), 40)

    def test_week_value_full_availability(self):
        for n in range(1, 6):
            self.assertAlmostEqual(model.LockIn(35, 11, 1.0, n).week_value(), 35 + model.best_of_c(n) * 11, delta=0.05)

    def test_games_count_beats_raw_average(self):
        x3 = model.LockIn(35, 11, 1.0, 3).week_value()
        y4 = model.LockIn(32, 12, 1.0, 4).week_value()
        x2 = model.LockIn(35, 11, 1.0, 2).week_value()
        self.assertAlmostEqual(x3, 41.9, delta=0.1)
        self.assertAlmostEqual(y4, 41.5, delta=0.1)
        self.assertLess(x2, y4)

    def test_bench_swap_example(self):
        starter = model.LockIn(30, 10, 1.0, 1).week_value(1, current=22)
        bench = model.LockIn(32, 11, 1.0, 3).week_value()
        self.assertAlmostEqual(starter, 30, delta=0.05)
        self.assertAlmostEqual(lineup.swap_gain(starter, bench), 8.9, delta=0.1)

    def test_per_game_avail(self):
        # One a for all games gives the old answer; a game he likely sits makes waiting worth less.
        same = model.LockIn(35, 11, 0.8, 3, avail=[0.8] * 3)
        self.assertAlmostEqual(same.week_value(), model.LockIn(35, 11, 0.8, 3).week_value())
        sits = model.LockIn(35, 11, 0.8, 3, avail=[0.8, 0.8, 0.1])  # likely sits the last game
        self.assertLess(sits.threshold(2), same.threshold(2))
        self.assertLess(sits.week_value(), same.week_value())
        self.assertEqual(model.game_avail(0.82, '', 33.0, False), 0.95)
        self.assertEqual(model.game_avail(0.82, 'GTD', 33.0, False), 0.82)
        self.assertEqual(model.game_avail(0.82, '', None, False), 0.82)
        self.assertEqual(model.game_avail(0.82, '', None, True), 0.1)

    def test_bench_game_does_not_count(self):
        gs = [dict(date='mon', fp=30.0), dict(date='wed', fp=48.8)]
        never = lambda d, fp: False
        bench_wed = model.week_state(gs, lambda d: d != 'wed', never)
        self.assertEqual(bench_wed['current'], 30.0)
        self.assertFalse(bench_wed['locked'])
        self.assertIsNone(model.week_state(gs, lambda d: False, never)['current'])
        mon = model.week_state(gs, lambda d: True, lambda d, fp: d == 'mon')  # locked Mon: Wed no longer counts
        self.assertEqual((mon['current'], mon['locked']), (30.0, True))
        self.assertFalse(model.week_state(gs, lambda d: d != 'mon', lambda d, fp: d == 'mon')['locked'])  # bench game: no lock

    def test_per_game_means(self):
        # Same mean for every game gives the old answer; one weak game lands between 2 and 3 good games.
        self.assertAlmostEqual(model.LockIn(35, 11, 1.0, 3, [35, 35, 35]).week_value(), model.LockIn(35, 11, 1.0, 3).week_value())
        two, three = model.LockIn(35, 11, 1.0, 2).week_value(), model.LockIn(35, 11, 1.0, 3).week_value()
        for mus in ([25, 35, 35], [35, 25, 35], [35, 35, 25]):
            self.assertTrue(two < model.LockIn(35, 11, 1.0, 3, mus).week_value() < three, mus)

    def test_week_mus_mix(self):
        self.assertEqual(model.week_mus(35, [29, None, 31]), [32, 35, 33])

    def test_simulation_matches_exact(self):
        lk = model.LockIn(40, 12, 0.8, 4)
        xs = lk.simulate(20000)
        self.assertAlmostEqual(sum(xs) / len(xs), lk.week_value(), delta=0.5)


class PlayerPool(unittest.TestCase):
    """The plan's pool of players across the season (about 3.5 games per week)."""

    def test_steady_star_never_moves(self):
        for n in (3, 10, 20, 40):
            self.assertAlmostEqual(model.belief(vet(), games(n, 50))['mu'], 50, delta=0.01)

    def test_steady_star_trust_grows(self):
        t = [model.belief(vet(), games(n, 50))['trust'] for n in (3, 10, 20, 40)]
        self.assertEqual(t, sorted(t))
        self.assertTrue(0.15 < t[0] < 0.35 and 0.4 < t[1] < 0.6 and t[3] > 0.7, t)

    def test_slumping_star_moves_slowly(self):
        p = vet(48)
        mu = [model.belief(p, games(n, 42))['mu'] for n in (3, 10, 20, 40)]
        self.assertGreater(mu[0], 46)          # week 1: no panic
        self.assertTrue(43.5 < mu[2] < 45.5)   # week 6: clearly lower
        self.assertEqual(mu, sorted(mu, reverse=True))

    def test_bench_breakout_follows_role(self):
        p = dict(fpg=18, sigma=11.9, tau=8.0, a=0.8, minutes=22.5, src='proj')
        hot = model.belief(p, games(4, 34, 34))
        self.assertGreater(hot['trust'], 0.5)
        self.assertGreater(hot['mu'], 26)
        back = model.belief(p, games(4, 34, 34) + games(6, 19, 22))  # teammate returns
        self.assertLess(back['mu'], 23)

    def test_unknown_player_switches_fast(self):
        u, v = model.prior_unknown(), vet(20, 12.3)
        self.assertLess(model.stabilize_games(u), 3)
        self.assertGreater(model.stabilize_games(v), 9)

    def test_injury_lowers_chance_to_play(self):
        p = vet(40)
        healthy = model.belief(p, games(5, 40), team_games=5)
        missed = model.belief(p, games(2, 40), team_games=5)
        out = model.belief(p, games(5, 40), team_games=5, injury='Out')
        self.assertLess(missed['a'], healthy['a'])
        self.assertLessEqual(out['a'], 0.1)
        self.assertAlmostEqual(missed['mu'], healthy['mu'], delta=0.01)  # per game holds, weekly value drops

    def test_seed_prior_is_less_sure_when_market_disagrees(self):
        row = dict(fpg='35', rw_fpg='35', espn_fpg='35', sigma='15', proj_gp='70', last_gp='70', adp_rank='30', rank='30')
        a = model.prior_from_seed(row, 34)
        b = model.prior_from_seed(dict(row, adp_rank='110'), 34)
        self.assertGreater(b['tau'], a['tau'])


class Scoring(unittest.TestCase):
    def test_fantasy_points(self):
        st = dict(pts=30, ast=5, stl=1, blk=1, fgm=11, fga=20, fta=8, ftm=6, dreb=6, oreb=2, to=3, pf=2)
        want = 60 + 7.5 + 2 + 1.5 + 8.25 - 30 - 1.5 + 3 + 3 - 6 - 1.5
        self.assertAlmostEqual(model.fantasy_points(st, SCORING), want)


class Lineups(unittest.TestCase):
    def test_best_lineup_is_legal_and_best(self):
        ps = [('pg1', ['PG'], 40), ('pg2', ['PG'], 39), ('sg', ['SG'], 20), ('sf', ['SF'], 25), ('pf', ['PF'], 26),
              ('f', ['SF', 'PF'], 30), ('c1', ['C'], 35), ('c2', ['C'], 34), ('w', ['SG', 'SF'], 22), ('x', ['PG'], 5)]
        tot, picks = lineup.best_lineup(ps)
        self.assertEqual(len(picks), 9)
        for i, pid in picks.items():
            self.assertTrue(lineup.fits(dict((p[0], p[1]) for p in ps)[pid], lineup.SLOTS[i]))
        self.assertNotIn('x', picks.values())
        self.assertEqual(tot, 40 + 39 + 20 + 25 + 26 + 30 + 35 + 34 + 22)

    def test_position_need(self):
        # Two centers: dropping the only C-eligible starter costs more than his points.
        base = [('c', ['C'], 20)] + [(f'p{i}', ['PG', 'SG', 'SF', 'PF'], 30) for i in range(8)] + [('b', ['PG'], 25)]
        no_c = [p for p in base if p[0] != 'c'] + [('w', ['SF'], 21)]
        self.assertLess(lineup.best_lineup(no_c)[0], lineup.best_lineup(base)[0])


if __name__ == '__main__':
    unittest.main()
