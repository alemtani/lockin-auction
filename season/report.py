"""Email text: Monday (lineup, matchup, waivers) and daily (results, lock calls, swaps)."""
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
CALL_COLOR = {'LOCK': '#0a7d32', 'CLOSE CALL': '#b26a00', 'COIN FLIP': '#b26a00', 'KEEP ROLLING': '#1d5fbf', 'LOCKED': '#555', 'FINAL': '#555', 'bench': '#999'}


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


def render(R):
    D = Doc()
    wk = R['week_dates']
    if R['mode'] == 'monday':
        subject = f"Lock-In week {R['week']}: lineup + pickups ({d(wk[0])}–{d(wk[-1])})"
    else:
        subject = f"Lock-In {d(R['day'])}: results + lock calls"

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
            rows, colors, reasons = [], {}, []
            for i, x in enumerate(res):
                rows.append([x['name'], f"{x['fp']:.1f}", f"{x['mu']:.0f}", f"{x['min']:.0f}", x['left'], x['call']])
                colors[i] = CALL_COLOR.get(x['call'].split(':')[0])
                if x['call'].split(':')[0] in ('LOCK', 'CLOSE CALL', 'KEEP ROLLING'):
                    ahead = (f"His last game this week is worth about {x['thr']:.1f}" if x['left'] == 1 else
                             f"With {x['left']} games left, his typical best is {x['thr']:.1f}")
                    gap = abs(x['fp'] - x['thr'])
                    end = {'LOCK': f"{x['fp']:.1f} beats it by {gap:.1f}. Lock it.",
                           'CLOSE CALL: LOCK': f"They are {gap:.1f} apart. That is a close call. You are the favorite, so play it safe. Lock it.",
                           'CLOSE CALL: ROLL': f"They are {gap:.1f} apart. That is a close call. You are the underdog, so take the chance. Keep rolling.",
                           'KEEP ROLLING': f"That is {gap:.1f} more. Keep rolling."}[x['call']]
                    reasons.append(f"{x['name']}: scored {x['fp']:.1f}. {ahead}. {end}")
            reasons.append(f"Close call = within {R['tie']:.1f} points. The lineup uses the same rule.") if any(x['call'].startswith('CLOSE CALL') for x in res) else None
            D.table(['Player', 'Score', 'Avg exp', 'Minutes', 'Games left', 'Call'], rows, colors)
            if reasons:
                D.head('Why')
                D.bullets(reasons)
            D.para("'Typical best' = what you can expect if you pass on this game and lock well later. "
                   "It is not the best of the games left, because you can't go back to a game you passed. "
                   "Lock when tonight beats it. 'Avg exp' = his usual game; the call also uses the matchup and injury news for the games left. "
                   "Sleeper doesn't share your locks, so this email assumes you followed every call.", muted=True)
        D.head('Bench swaps')
        if R.get('swaps'):
            D.bullets([f"{s['slot']}: start {s['into']} for {s['out']}. +{s['gain']:.1f} points. {s['into']} has {s['in_games']} games left (worth about {s['in_v']:.1f}); "
                       f"{s['out']} is worth about {s['out_v']:.1f} from here." for s in R['swaps']])
        else:
            D.para('No swap worth 3+ points.')

    D.head('Every player this week' if R['mode'] == 'monday' else 'Every player from today')

    def note(x):
        av = x.get('avail') or []
        bits = [x['injury']] if x['injury'] else []
        if av and min(av) < 0.75:
            bits.append('plays ' + '/'.join(f"{a * 100:.0f}" for a in av) + '%')
        return ', '.join(bits)
    rows = [[x['slot'], x['name'] + ('*' if x['flip'] else ''), x['games'], f"{x['value']:.1f}", f"{x['mu']:.1f}", note(x)] for x in R['lineup']]
    rows += [['IR' if b['ir'] else 'BN', b['name'] + ('*' if b['flip'] else ''), b['games'], f"{b['value']:.1f}", f"{b['mu']:.1f}", note(b)]
             for b in R['bench']]
    D.table(['Slot', 'Player', 'Games', 'Week score', 'Per game', 'Note'], rows)
    if R.get('close'):
        if R.get('fav') is None:
            rule = 'No matchup found, so the higher score starts.'
        elif R['fav']:
            rule = 'You are the favorite, so close calls go to the steadier lineup.'
        else:
            rule = 'You are the underdog, so close calls go to the boom-or-bust lineup.'
        D.para(f"* Close calls (within {R['tie']:.1f} points; the same rule as a close lock call). {rule}", muted=True)
        D.bullets([f"{c['a']} {c['a_v']:.1f} (swing ±{c['a_sd']:.0f}) vs {c['b']} {c['b_v']:.1f} (±{c['b_sd']:.0f}), "
                   f"{c['margin']:.1f} apart. Start {c['start']}." for c in R['close']])
    if R.get('empty'):
        D.para(f"Warning: your Sleeper lineup has an empty slot ({', '.join(R['empty'])}). An empty slot scores 0.",
               f"<b>Warning: your Sleeper lineup has an empty slot ({H.escape(', '.join(R['empty']))}).</b> An empty slot scores 0.")
    if R['changes_in']:
        txt = f"start {', '.join(R['changes_in'])}" + (f"; bench {', '.join(R['changes'])}" if R['changes'] else '')
        D.para(f"Change in Sleeper: {txt}.", f"<b>Change in Sleeper:</b> {H.escape(txt)}.")
    else:
        D.para('Your Sleeper lineup already matches.')
    D.para("'Week score' = expected locked score for the week. It counts the games left and the chance he plays, "
           "so a 4-game player can beat a better 3-game player. 'plays' = his chance to play each game left. "
           "Status can change during the week: an Out player can be back later, and Sleeper's return date is in these numbers. "
           "The daily email re-checks every player.", muted=True)
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
            tag = '' if w['ok'] else ' (not worth it yet)' if w['gain'] >= 0.5 else ' (no gain: he would not crack your top 12)'
            D.para(f"{i + 1}. Add {w['add']['name']} ({w['add']['pos']} {w['add']['team']}), drop {w['drop']}: +{w['gain']:.1f} per week{tag}.",
                   f"<b>{i + 1}. Add {H.escape(w['add']['name'])}</b> ({H.escape(w['add']['pos'])} {H.escape(w['add']['team'])}), drop {H.escape(w['drop'])}: <b>+{w['gain']:.1f}</b> per week{tag}.")
            D.para(why_pickup(w), muted=True)
        bar = "+2 for a free agent, +4 if it costs waiver priority" if R['mode'] == 'monday' else "+4 for a free agent, +8 if it costs waiver priority (twice the Monday bar, so you don't churn mid-week)"
        D.para(f"Only rising players are listed: beating their projection, or minutes up. "
               f"Gain = how much better your lineup gets this week (plus half of next week). A move needs {bar}.", muted=True)

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
