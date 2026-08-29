import json, re, unicodedata
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if not unicodedata.combining(c)).lower()

roster = Roster.load()
pricer = Pricer()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))

bad = []
for entry in corpus['matches']:
    m = entry['match']
    a, b = players_from_match(m)
    ctx = roster.context(a, b)
    for mk in entry['markets']:
        q = mk['question']
        cm = re.match(r"Will (.+?) (?:record more (?:aces|double faults) than (.+?)|win)\?$", q)
        if not cm:
            continue
        first = fold(cm.group(1))
        spec = parse_question(q, players=(ctx.player_a.name, ctx.player_b.name))
        truth = ctx.player_a if fold(a) == first else (ctx.player_b if fold(b) == first else None)
        subj, _ = pricer._orient(spec, ctx)
        if truth is None or subj is not truth:
            bad.append((q, a, b, ctx.player_a.name, ctx.player_b.name, spec.subject,
                        None if subj is None else subj.name))
print("mis-oriented:", len(bad))
for r in bad: print(r)
print()
print("roster misses:", roster.miss_report)
print("unrated players in draw:")
for entry in corpus['matches']:
    a, b = players_from_match(entry['match'])
    ctx = roster.context(a, b)
    for p, raw in ((ctx.player_a, a), (ctx.player_b, b)):
        if not p.has_ratings:
            print("   ", raw, "->", p.name)
