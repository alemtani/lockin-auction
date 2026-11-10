"""Email text: Monday (lineup, matchup, waivers) and daily (last night, lock calls, lineup)."""
import datetime, html as H, os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

STYLE = """<style>
.lk{max-width:640px;font:14px/1.45 -apple-system,Segoe UI,sans-serif}
.lk h3{font-size:16px;margin:18px 0 6px}
.lk p,.lk ul{margin:6px 0}.lk ul{padding-left:18px}
.lk table{border-collapse:collapse;font-size:13px;width:100%}
.lk th{text-align:left;padding:4px 6px;border-bottom:2px solid #ccc;color:#555}
.lk td{padding:4px 6px;border-bottom:1px solid #eee;vertical-align:top}
.lk .m{color:#777;font-size:12px}
</style>"""
CALL_COLOR = {'LOCK': '#0a7d32', 'ROLL': '#1d5fbf', 'WAIT': '#b26a00', 'HOLD': '#999', 'DROP': '#999', 'CLOSE CALL': '#b26a00', 'COIN FLIP': '#b26a00', 'KEEP ROLLING': '#1d5fbf', 'LOCKED': '#555', 'FINAL': '#555', 'bench': '#999'}


def d(day):
    return datetime.date.fromisoformat(day).strftime('%a %-m/%-d')


def f1(x):
    return '—' if x is None else f'{x:.1f}'


class Doc:
    def __init__(self):
        self.h, self.t = [], []

    def head(self, s):
        self.h.append(f'<h3>{H.escape(s)}</h3>')
        self.t.append('\n' + s.upper())

    def para(self, s, html=None, muted=False):
        self.h.append(f'<p{" class=m" if muted else ""}>{html or H.escape(s)}</p>')
        self.t.append(s)

    def bullets(self, items):
        self.h.append('<ul>' + ''.join(f'<li>{H.escape(i)}</li>' for i in items) + '</ul>')
        self.t.extend('- ' + i for i in items)

    def table(self, cols, rows, colors=None):
        th = ''.join(f'<th>{H.escape(c)}</th>' for c in cols)
        body = ''
        for i, r in enumerate(rows):
            c = (colors or {}).get(i)
            body += '<tr>' + ''.join(f'<td{f" style=color:{c};font-weight:600" if c and j == len(r) - 1 else ""}>{H.escape(str(v))}</td>'
                                     for j, v in enumerate(r)) + '</tr>'
        self.h.append(f'<table><tr>{th}</tr>{body}</table>')
        w = [max(len(str(x)) for x in col) for col in zip(cols, *rows)]
        self.t.append('  '.join(str(c).ljust(n) for c, n in zip(cols, w)))
        self.t.extend('  '.join(str(v).ljust(n) for v, n in zip(r, w)) for r in rows)

    def out(self):
        return STYLE + '<div class="lk">' + '\n'.join(self.h) + '</div>', '\n'.join(self.t)


def why_pickup(m):
    c = m['add']
    bits = []
    if c['n']:
        bits.append(f"averaging {c['avg']:.0f} in {c['n']} games vs projected {c['prior']:.0f} ({c['avg'] - c['prior']:+.0f}), last 3: {c['last3']:.0f} → we now expect {c['mu']:.0f} (trust in real games {c['trust']:.0%})")
    else:
        bits.append(f"projected {c['mu']:.0f} per game, no games yet")
    if m['role_up']:
        bits.append(f"minutes up: {c['prior_min']:.0f} → {c['recent_min']:.0f}. Check if a teammate is out; the boost may end when he returns")
    bits.append(f"{m['games']} games left this week, {m['games_next']} next")
    if m['claim']:
        bits.append('on waivers: a claim uses your priority')
    return '; '.join(bits)


def call_line(c, tie):
    """One morning call in plain words, with the numbers behind it."""
    n, x, bar = c['name'], c['x'], c.get('bar')
    close = f" Close call ({abs(x - bar):.1f} apart, within {tie:.1f})." if c.get('close') else ''
    nxt = f"{c['next_game']}, his next game" if c.get('next_game') else None
    if c['kind'] == 'lock':
        moved = f", moved from {c['from_slot']}" if c['from_slot'] != c['slot'] else ''
        if c.get('now'):
            return (f"Lock {n} ({x:.1f}) at {c['slot']} now{moved}. He needs {bar:.0f}, so it is safe by {x - bar:.0f}. "
                    f"Waiting for his next game{' (' + c['next_game'] + ')' if c.get('next_game') else ''} gains nothing, "
                    "and a missed morning would let that game replace it.")
        why = ' You are the favorite, so play it safe.' if c.get('close') else ''
        return f"Lock {n} ({x:.1f}) at {c['slot']}{moved}. He needed {bar:.1f}.{close}{why}"
    if c['kind'] == 'roll':
        why = ' You are the underdog, so take the chance.' if c.get('close') else ''
        return f"Don't lock {n} ({x:.1f}). He plays today, and that game replaces it. Locking needed {bar:.1f}.{close}{why}"
    if c['kind'] == 'keep':
        if not nxt:
            return f"{n} holds {x:.1f} at {c['slot']}. He has no games left, so it counts."
        return (f"{n} holds {x:.1f} at {c['slot']} and needs {bar:.0f}. No game today, so it waits. "
                f"Lock it on {nxt}, unless the bar moves.")
    plan = ''
    if c.get('plan_slot'):
        plan = (f" Fresh, {c['plan_slot']} is worth {c['plan_worth']:.0f} from these tries: {', '.join(c['plan_tries'])}.")
    if c['kind'] == 'hold':
        return (f"{n} holds {x:.1f} at {c['slot']} for today only. He needs {bar:.0f}, but nobody else plays {c['slot']} today, "
                f"so keeping him costs nothing. Expect to bench him {('on ' + nxt) if nxt else 'later'}.{plan}")
    if c.get('by'):
        return f"Bench {n}: his {x:.1f} is below his bar, and {c['by']} locks in his slot ({c['from_slot']}). Benching drops it."
    need = f", and he needs {bar:.0f}" if bar is not None else ''
    return f"Bench {n}: his {x:.1f} is below the bar{need}. A player who plays today needs his slot. Benching drops it.{plan}"


def render(R):
    D = Doc()
    wk = R['week_dates']
    if R['mode'] == 'monday':
        subject = f"Lock-In week {R['week']}: lineup + pickups ({d(wk[0])}–{d(wk[-1])})"
    else:
        subject = f"Lock-In {d(R['day'])}: lock calls + lineup"

    m = R.get('matchup')
    if m:
        D.para(f"Week {R['week']} vs {m['opp']}: you {m['me']:.0f}, them {m['them']:.0f} (expected). Win chance {m['chance']:.0%}.",
               f"Week {R['week']} vs <b>{H.escape(m['opp'])}</b>: you <b>{m['me']:.0f}</b>, them <b>{m['them']:.0f}</b> (expected). Win chance <b>{m['chance']:.0%}</b>.")

    if R['mode'] == 'daily':
        D.head(f"Last night ({d(R['yesterday'])})")
        res = R.get('results') or []
        if not res:
            D.para('None of your players played.')
        else:
            rows = [[x['name'], f"{x['fp']:.1f}", f"{x['mu']:.0f}", f"{x['min']:.0f}", x['call']] for x in res]
            colors = {i: CALL_COLOR.get(x['call'].split()[0]) for i, x in enumerate(res)}
            D.table(['Player', 'Score', 'Avg exp', 'Minutes', 'Call'], rows, colors)

    D.head('Lock calls this morning')
    order = {'lock': 0, 'roll': 1, 'drop': 2, 'hold': 3, 'keep': 4}
    cs = sorted(R.get('calls') or [], key=lambda c: (order[c['kind']], -c['x']))
    if cs:
        D.bullets([call_line(c, R['tie']) for c in cs])
    else:
        D.para('None. No player has an open score.')
    D.para("A score can wait until the player's next game. The call comes the morning he plays again: lock it, "
           "or today's game replaces it. A waiting score locks now when it beats its bar by 10 or more. "
           "'Needs' = what your other slots give up without him, so the bar depends on your whole roster, "
           "not just his own games. A lock goes in the most restrictive slot he fits. A waiting player stays in his "
           "slot, and a score below its bar is benched only when someone who plays today needs the slot. "
           "Sleeper doesn't share your locks, so this email assumes you followed every call. If you didn't, "
           "add a line to data/season/locks.txt.", muted=True)

    D.head("Today's lineup")

    def note(x):
        av = x.get('avail') or []
        bits = [x['injury']] if x['injury'] else []
        if av and min(av) < 0.75:
            bits.append('plays ' + '/'.join(f"{a * 100:.0f}" for a in av) + '%')
        return ', '.join(bits)
    rows = [[x['slot'], x['name'], x['today'], x['bar'], x['if_below'], note(x)] for x in R['plan']]
    rows += [[b['slot'], b['name'], b['today'], '', '', note(b)] for b in R['bench']]
    D.table(['Slot', 'Player', 'Today', 'Lock if >=', 'If below', 'Note'], rows)
    if R.get('lock_mismatch'):
        names = ', '.join(R['lock_mismatch'])
        who = 'that player' if len(R['lock_mismatch']) == 1 else 'those players'
        msg = (f"the email has a lock on record for {names}, but Sleeper does not list {who} as starting. "
               "A locked player can't move, so the record is wrong. Fix it in data/season/locks.txt.")
        D.para('Check: ' + msg, '<b>Check:</b> ' + H.escape(msg))
    if R.get('empty'):
        D.para(f"Warning: your Sleeper lineup has an empty slot ({', '.join(R['empty'])}). An empty slot scores 0.",
               f"<b>Warning: your Sleeper lineup has an empty slot ({H.escape(', '.join(R['empty']))}).</b> An empty slot scores 0.")
    if R['moves']:
        D.para('Change in Sleeper: ' + '; '.join(R['moves']) + '.',
               '<b>Change in Sleeper:</b> ' + H.escape('; '.join(R['moves'])) + '.')
    else:
        D.para('Your Sleeper lineup already matches.')
    D.para("'Free try' = the slot holds no score, so any score helps and you can still replace it. "
           "'Lock if >=' = the score he needs, judged the morning of his next game. 'If below' = what the slot does then. "
           "'plays' = his chance to play each game left. Status can change during the week: an Out player can be back "
           "later, and Sleeper's return date is in these numbers. The email re-checks every player each morning.", muted=True)

    if R.get('week_plan'):
        D.head('Week plan (re-planned every morning)')
        D.bullets([f"{w['slot']}: {w['held']}" if 'held' in w else
                   f"{w['slot']} (worth {w['worth']:.0f}): " + (', '.join(w['tries']) or 'no games left') for w in R['week_plan']])
        D.para("Who tries each open slot, by day. Each player has one home slot, so his games count once. "
               "'Worth' = what the slot can expect from these tries: lock a try that beats what the later tries "
               "can still expect, else roll to the next one.", muted=True)
    if m:
        D.para(f"Their best: " + ', '.join(f"{n} {v:.0f}" for n, v in m['their_top']) + '.', muted=True)

    if 'waivers' in R:
        D.head('Waiver pickups')
        ws = R['waivers']
        good = [w for w in ws if w['ok']]
        if not good:
            D.para('No move. No rising free agent beats your roster by enough. The closest:' if ws else
                   'No free agent is beating his projection or gaining minutes.')
        for i, w in enumerate(ws):
            g = round(w['gain'], 1) + 0.0  # no '-0.0'
            tag = '' if w['ok'] else ' (not enough)' if g > 0 else ' (no gain)' if g == 0 else ' (worse)'
            D.para(f"{i + 1}. Add {w['add']['name']} ({w['add']['pos']} {w['add']['team']}), drop {w['drop']}: {g:+.1f} per week{tag}.",
                   f"<b>{i + 1}. Add {H.escape(w['add']['name'])}</b> ({H.escape(w['add']['pos'])} {H.escape(w['add']['team'])}), drop {H.escape(w['drop'])}: <b>{g:+.1f}</b> per week{tag}.")
            D.para(why_pickup(w), muted=True)
        bar = "+2 for a free agent, +4 if it costs waiver priority" if R['mode'] == 'monday' else "+4 for a free agent, +8 if it costs waiver priority (twice the Monday bar, so you don't churn mid-week)"
        D.para(f"Only rising players are listed: beating their projection, or minutes up. "
               f"Gain = how much more your open slots expect this week with him instead, plus half of next week, "
               f"from the same slot plan as the lock calls. Your 13th and 14th players count, because they get tries. "
               f"A move needs {bar}.", muted=True)

    if R.get('movers'):
        D.head('Outlook changes this week')
        D.bullets([f"{n}: {a:.1f} → {b:.1f} per game" for n, a, b in R['movers']])

    D.head('Your players: projection vs real games')
    rows = []
    for c in sorted(R['cards'].values(), key=lambda c: -c['mu']):
        rows.append([c['name'], f"{c['prior']:.1f}", f1(c['avg']), f1(c['last3']), c['n'], f"{c['mu']:.1f}", f"{c['trust']:.0%}", f"{c['k']:.0f}"])
    D.table(['Player', 'Projected', 'Real avg', 'Last 3', 'Games', 'Now', 'Trust real', '50/50 at'], rows)
    D.para("'Real avg' counts every game, locked or not, so a run of duds pulls 'Now' down. 'Last 3' shows the recent trend. "
           "'Trust real' = how much real games count vs the projection. '50/50 at' = games until they count the same. "
           "Steady veterans take ~10 games; uncertain players switch faster.", muted=True)

    h, t = D.out()
    return subject, h, t


def send(subject, html, text, to):
    user, pw = os.environ.get('GMAIL_USER', to), os.environ['GMAIL_APP_PASSWORD']
    msg = MIMEMultipart('alternative')
    msg['Subject'], msg['From'], msg['To'] = subject, user, to
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(html, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(user, pw)
        s.send_message(msg)
