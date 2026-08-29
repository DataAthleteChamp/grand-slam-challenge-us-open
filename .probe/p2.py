"""Check orientation symmetry of the ace / DF comparison route."""
import json
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))
roster = Roster.load()
pricer = Pricer()

worst = []
for entry in corpus['matches']:
    match = entry['match']
    a, b = players_from_match(match)
    ctx = roster.context(a, b)
    for mk in entry['markets']:
        q = mk['question']
        if 'aces' not in q and 'double faults' not in q:
            continue
        spec = parse_question(q, players=(ctx.player_a.name, ctx.player_b.name))
        p1 = pricer.price(spec, ctx, market_id='x')
        # mirrored question: swap the two names in the question text
        stat = 'aces' if 'aces' in q else 'double faults'
        # rebuild "Will <B> record more X than <A>?"
        import re
        m = re.match(r"Will (.+?) record more (aces|double faults) than (.+?)\?$", q)
        assert m, q
        first, s, second = m.group(1), m.group(2), m.group(3)
        mirrored = f"Will {second} record more {s} than {first}?"
        spec2 = parse_question(mirrored, players=(ctx.player_a.name, ctx.player_b.name))
        p2 = pricer.price(spec2, ctx, market_id='y')
        total = p1.probability + p2.probability
        worst.append((abs(total - 1.0), total, q, mirrored, p1.probability, p2.probability,
                      ctx.player_a.name, ctx.player_b.name,
                      ctx.player_a.height, ctx.player_b.height, stat))

worst.sort(reverse=True)
print("P(A>B) + P(B>A) should equal 1 - P(tie), i.e. ~0.84 (DF) / ~0.92 (aces) for ATP")
for w in worst[:12]:
    print(f"sum={w[1]:.4f}  {w[2][:55]:55s} p1={w[4]:.3f} p2={w[5]:.3f} "
          f"h={w[8]}/{w[9]} {w[10]}")
print()
import statistics
aces = [w[1] for w in worst if w[10]=='aces']
dfs = [w[1] for w in worst if w[10]=='double faults']
print("aces sums: min %.3f max %.3f median %.3f" % (min(aces), max(aces), statistics.median(aces)))
print("df   sums: min %.3f max %.3f median %.3f" % (min(dfs), max(dfs), statistics.median(dfs)))
