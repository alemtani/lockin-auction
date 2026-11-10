"""Add a line to data/season/locks.txt: your own lock record, for a call you did not follow.

  python3 season/add_lock.py --player durant --call lock
  python3 season/add_lock.py --player "lamelo" --call roll --date 2026-11-10
  python3 season/add_lock.py --player durant --call lock --dry-run   print the line, change nothing

player: any part of the name of a player on your roster (not case sensitive). It must match one player.
date: the game's date. Blank = his latest game in data/season/games.csv (the score he holds now).
A line for the same player and date replaces the old one. The GitHub action "lock record" runs this.
"""
import argparse, csv, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import daily, fetch

GAMES = f'{daily.OUT}/games.csv'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--player', required=True)
    ap.add_argument('--call', required=True, choices=['lock', 'roll'])
    ap.add_argument('--date', default='')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    day = daily.today_pt()
    sched = fetch.schedule()
    week = daily.current_week(daily.week_dates(sched), day)
    L = fetch.league_data(week)
    players = fetch.players()
    mine = next(r for r in L['rosters'] if r['owner_id'] == fetch.ME)
    names = {pid: (players.get(pid) or {}).get('full_name') or pid for pid in mine['players'] or []}
    q = a.player.strip().lower()
    hits = [pid for pid, n in names.items() if q in n.lower()]
    if len(hits) != 1:
        sys.exit(f"'{a.player}' matches {len(hits)} of your players" + (f": {', '.join(names[p] for p in hits)}" if hits else '')
                 + '. Use more of the name.')
    pid, name = hits[0], names[hits[0]]

    date = a.date.strip()
    if date:
        try:
            daily.datetime.date.fromisoformat(date)
        except ValueError:
            sys.exit(f"date '{date}' is not YYYY-MM-DD.")
    else:
        played = sorted(r['date'] for r in csv.DictReader(open(GAMES), delimiter='|') if r['sleeper_id'] == pid)
        if not played:
            sys.exit(f'{name} has no games in {GAMES}. Give the date.')
        date = played[-1]

    line = f'{date}|{name}|{a.call}'
    print(line)
    if a.dry_run:
        return
    old = open(daily.LOCKS).read().splitlines() if os.path.exists(daily.LOCKS) else []
    keep = [l for l in old if l.split('#')[0].strip().lower().split('|')[:2] != [date, name.lower()]]
    open(daily.LOCKS, 'w').write('\n'.join(keep + [line]) + '\n')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        open(summary, 'a').write(f'Recorded: **{name}**, game of {date}: **{a.call}**.\n')


if __name__ == '__main__':
    main()
