"""Does the pricer's answer to 'Will A win?' and 'Will B win?' sum to 1?"""
import json
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

roster = Roster.load(); pricer = Pricer()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))
rows = []
for entry in corpus['matches'][:20]:
    m = entry['match']
    a, b = players_from_match(m)
    ctx = roster.context(a, b)
    if not (ctx.player_a.has_ratings and ctx.player_b.has_ratings):
        continue
    qa = f"Will {a} win?"; qb = f"Will {b} win?"
    pa = pricer.price(parse_question(qa, players=(ctx.player_a.name, ctx.player_b.name)), ctx)
    pb = pricer.price(parse_question(qb, players=(ctx.player_a.name, ctx.player_b.name)), ctx)
    rows.append((abs(pa.model_probability + pb.model_probability - 1.0),
                 m['name'], pa.model_probability, pb.model_probability,
                 pa.submission, pb.submission, ctx.tour))
rows.sort(reverse=True)
print(f"{'match':34s} {'P(A)':>7s} {'P(B)':>7s} {'sum':>7s}  subs")
for r in rows[:12]:
    print(f"{r[1][:34]:34s} {r[2]:7.4f} {r[3]:7.4f} {r[2]+r[3]:7.4f}  {r[4]}/{r[5]} {r[6]}")
print("\nmean |sum-1| over %d matches: %.4f, max %.4f" %
      (len(rows), sum(r[0] for r in rows)/len(rows), rows[0][0]))
