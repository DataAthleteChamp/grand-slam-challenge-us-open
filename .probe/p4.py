"""Verify the extracted subject is the player actually named first in the question."""
import json, re, unicodedata
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

roster = Roster.load()
pricer = Pricer()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))

bad = []
for entry in corpus['matches']:
    m = entry['match']
    a, b = players_from_match(m)          # e.g. "M. Giron", "I. Buse"
    ctx = roster.context(a, b)
    for mk in entry['markets']:
        q = mk['question']
        cm = re.match(r"Will (.+?) (?:record more (?:aces|double faults) than (.+?)|win)\?$", q)
        if not cm:
            continue
        named_first = cm.group(1)
        spec = parse_question(q, players=(ctx.player_a.name, ctx.player_b.name))
        # which roster player does the truth say it is?
        truth = ctx.player_a if named_first == a else (ctx.player_b if named_first == b else None)
        subj, opp = pricer._orient(spec, ctx)
        ok = (truth is not None) and (subj is truth)
        if not ok:
            bad.append((q, a, b, ctx.player_a.name, ctx.player_b.name, spec.subject,
                        None if subj is None else subj.name))

print("mis-oriented / unresolved-subject questions:", len(bad))
for row in bad:
    print(row)
