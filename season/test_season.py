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

    def test_out_tag_is_for_the_next_game(self):
        # Out now, Sleeper gives minutes later in the week (0, 0, 20, 20): back for the later games.
        av = [model.game_avail(0.1, 'Out', p, p is None, i == 0, 0.85) for i, p in enumerate([None, None, 18.0, 18.0])]
        self.assertEqual(av, [0.1, 0.1, 0.85, 0.85])
        # The tag still holds for his next game, and where Sleeper has no projection at all.
        self.assertEqual(model.game_avail(0.65, 'DTD', 30.0, False, True, 0.9), 0.65)
        self.assertEqual(model.game_avail(0.1, 'Out', None, False, False, 0.9), 0.1)

    def test_per_game_sigmas(self):
        # One sigma for all games gives the old answer. A wilder middle game raises the bar to lock the first.
        # (The last game's swing never matters: you keep it whatever it scores.)
        same = model.LockIn(30, 10, 1.0, 3)
        self.assertAlmostEqual(model.LockIn(30, 10, 1.0, 3, sigmas=[10, 10, 10]).threshold(2), same.threshold(2))
        self.assertGreater(model.LockIn(30, 10, 1.0, 3, sigmas=[10, 16, 10]).threshold(2), same.threshold(2))

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


class Planner(unittest.TestCase):
    """The slot planner (planner.py): one move each morning."""

    @staticmethod
    def pool(**ps):
        # name=(positions, {day: mu}) with a = 1.0 and sigma = 10
        return {n: dict(name=n, pos=pos, games={d: (mu, 1.0, 10.0) for d, mu in g.items()}) for n, (pos, g) in ps.items()}

    def setUp(self):
        import planner
        self.P = planner

    def test_one_home_slot_per_player(self):
        # One C who plays 3 times: only the C slot can use him, and he counts once, not in UTIL too.
        pl = self.pool(c=(['C'], {1: 30, 2: 30, 3: 30}))
        st = self.P.streams(pl, [1, 2, 3], [6, 7])
        self.assertEqual(sum(len(v) for v in st.values()), 3)

    def test_empty_slot_takes_a_free_try(self):
        pl = self.pool(g=(['PG'], {1: 15}), star=(['PG'], {2: 40}))
        new, _, calls = self.P.morning(1, [1, 2], {}, {}, set(), pl)
        self.assertIn('g', new.values())  # he plays today; the star plays tomorrow
        self.assertEqual(calls, [])

    def test_big_score_locks_small_score_rolls(self):
        pl = self.pool(a=(['C'], {2: 30, 3: 30}))
        for x, kind in ((60.0, 'lock'), (10.0, 'roll')):
            holder, score, locked = {6: 'a'}, {'a': x}, set()
            _, _, calls = self.P.morning(2, [2, 3], holder, score, locked, pl)
            self.assertEqual(calls[0]['kind'], kind)
            self.assertEqual(bool(locked), kind == 'lock')  # any slot he fits; with no one else, all cost the same

    def test_score_waits_when_he_has_no_game_today(self):
        # He holds 33 and plays again on day 3. No call today: his score waits.
        pl = self.pool(a=(['C'], {3: 30}), b=(['PG'], {2: 25}))
        _, _, calls = self.P.morning(2, [2, 3], {6: 'a'}, {'a': 33.0}, set(), pl)
        self.assertEqual([c['kind'] for c in calls], ['keep'])

    def test_close_call_follows_the_matchup(self):
        pl = self.pool(a=(['C'], {2: 30, 3: 30}))
        bar = self.P.lock_options('a', 6, [2, 3], {6: 'a'}, {'a': 0}, set(), pl)[0][0]
        for safe, kind in ((True, 'lock'), (False, 'roll')):
            _, _, calls = self.P.morning(2, [2, 3], {6: 'a'}, {'a': bar - 0.5}, set(), pl, tie=1.5, safe=safe)
            self.assertEqual(calls[0]['kind'], kind)
            self.assertTrue(calls[0]['close'])

    def test_waiting_player_stays_put(self):
        # Last night: a (C/PF, holds 50, plays day 3) sat at PF. Today c (C) plays. a stays at PF, c takes C.
        pl = self.pool(a=(['C', 'PF'], {3: 30}), c=(['C'], {2: 25, 3: 25}))
        new, _, calls = self.P.morning(2, [2, 3], {4: 'a'}, {'a': 50.0}, set(), pl, lock_now=99)
        self.assertEqual((new.get(4), new.get(6)), ('a', 'c'))
        self.assertEqual(calls[0]['kind'], 'keep')

    def test_big_waiting_score_locks_now(self):
        pl = self.pool(a=(['C'], {3: 30}))
        locked = set()
        _, _, calls = self.P.morning(2, [2, 3], {6: 'a'}, {'a': 70.0}, locked, pl)
        self.assertEqual((calls[0]['kind'], calls[0]['now']), ('lock', True))
        self.assertEqual(locked, {6})

    def test_lock_takes_the_most_restrictive_slot(self):
        # A PG with nobody else: every slot costs the same, so he locks at PG, not G or UTIL.
        pl = self.pool(a=(['PG'], {2: 30, 3: 30}))
        _, _, calls = self.P.morning(2, [2, 3], {7: 'a'}, {'a': 80.0}, set(), pl)
        self.assertEqual(calls[0]['slot'], 0)

    def test_low_score_holds_until_his_slot_is_needed(self):
        # a (C) holds 5, below his bar, and plays day 3. Nobody else plays C today: he holds.
        pl = self.pool(a=(['C'], {3: 30}), g=(['PG'], {2: 20}))
        new, _, calls = self.P.morning(2, [2, 3], {6: 'a'}, {'a': 5.0}, set(), pl)
        self.assertEqual((calls[0]['kind'], new.get(6)), ('hold', 'a'))
        # Now c (C) plays today, both UTIL slots are locked, so c needs C: a is benched and his score is gone.
        pl = self.pool(a=(['C'], {3: 30}), c=(['C'], {2: 25}), u=(['PG'], {}), v=(['PG'], {}))
        new, _, calls = self.P.morning(2, [2, 3], {6: 'a', 7: 'u', 8: 'v'}, {'a': 5.0, 'u': 40.0, 'v': 40.0}, {7, 8}, pl)
        self.assertEqual((calls[0]['kind'], new.get(6)), ('drop', 'c'))

    def test_long_shot_today_beats_a_dead_score(self):
        # a (C/PF) holds 3 at PF, far below his bar, and plays day 3. m (PF/SF) is tagged Out today (10%), and his
        # home slot is F (d plays F today). Nobody's plan uses PF today, but a's 3 almost surely gets replaced on
        # day 3, so m's long-shot try at PF is worth more than holding it.
        pl = self.pool(a=(['C', 'PF'], {3: 32}), m=(['PF', 'SF'], {2: 29, 3: 29}), d=(['SF'], {2: 25}))
        pl['m']['games'][2] = (29, 0.1, 13)
        holder, score, locked = {4: 'a', 5: 'd'}, {'a': 3.0}, set()
        for t in (0, 1, 2, 3, 6, 7, 8):
            pl[f'x{t}'] = dict(name='x', pos=['PG', 'SG', 'SF', 'PF', 'C'], games={})
            holder[t] = f'x{t}'
            score[f'x{t}'] = 40.0
            locked.add(t)
        new, _, calls = self.P.morning(2, [2, 3], holder, score, locked, pl, lock_now=99)
        self.assertEqual((new.get(4), new.get(5)), ('m', 'd'))
        self.assertEqual(next(c['kind'] for c in calls if c['p'] == 'a'), 'drop')

    def test_today_player_displaces_the_lowest_score(self):
        # g (PG) plays today. G and UTIL hold 30 and 8, UTIL 2 is locked: g takes the UTIL with 8.
        pl = self.pool(a=(['SG'], {3: 30}), b=(['SG'], {3: 30}), u=(['C'], {}), g=(['PG'], {2: 25}))
        holder = {2: 'a', 7: 'b', 8: 'u'}
        locked = {8}
        for t in (0, 1, 3, 4, 5, 6):  # every other slot locked by dummies
            pl[f'x{t}'] = dict(name='x', pos=['PG', 'SG', 'SF', 'PF', 'C'], games={})
            holder[t] = f'x{t}'
            locked.add(t)
        score = {'a': 30.0, 'b': 8.0, 'u': 40.0, **{f'x{t}': 40.0 for t in (0, 1, 3, 4, 5, 6)}}
        new, _, _ = self.P.morning(2, [2, 3], holder, score, locked, pl, lock_now=99)
        self.assertEqual((new.get(2), new.get(7)), ('a', 'g'))

    def test_lock_goes_where_backup_is_thinnest(self):
        # a (C/PF) holds 80 at PF. c (C) holds 5 at C, below his bar, and can't play PF. Three others fit PF.
        # Bars tie, so a locks at C (2 players fit it, 4 fit PF) and c is benched.
        pl = self.pool(a=(['C', 'PF'], {3: 30}), c=(['C'], {3: 20}),
                       f1=(['PF'], {2: 20, 3: 20}), f2=(['PF'], {2: 20}), f3=(['PF', 'SF'], {3: 20}))
        holder, score, locked = {4: 'a', 6: 'c'}, {'a': 80.0, 'c': 5.0}, set()
        new, _, calls = self.P.morning(2, [2, 3], holder, score, locked, pl)
        lock = next(c for c in calls if c['p'] == 'a')
        self.assertEqual((lock['kind'], lock['slot']), ('lock', 6))
        self.assertNotIn('c', new.values())

    def test_benched_score_is_gone(self):
        gs = [dict(date='mon', fp=30.0)]
        st = model.week_state(gs, lambda d: True, lambda d, fp: False, alive=lambda d: False)
        self.assertIsNone(st['current'])
