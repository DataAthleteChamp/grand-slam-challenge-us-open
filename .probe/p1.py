import json, sys
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))
roster = Roster.load()
pricer = Pricer()
rows = []
for entry in corpus['matches']:
    match = entry['match']
    name_a, name_b = players_from_match(match)
    ctx = roster.context(name_a, name_b, round_code=match.get('round'), tour=match.get('tour'))
    for mk in entry['markets']:
        q = mk['question']
        spec = parse_question(q, players=(ctx.player_a.name, ctx.player_b.name))
        pr = pricer.price(spec, ctx, market_id=mk['id'])
        rows.append((match['name'], q, spec, pr, ctx))

print("total", len(rows))
from collections import Counter
print(Counter(str(s.family) for _,_,s,_,_ in rows))
print(Counter((str(s.family), s.comparator, s.threshold) for _,_,s,_,_ in rows if s.family.value=='match_duration').most_common(5))
# show duration submissions
for name,q,s,p,c in rows:
    if s.family.value == 'match_duration':
        print(f"{c.tour} {name[:30]:30s} | {q[:50]:50s} | thr={s.threshold} cmp={s.comparator} model={p.model_probability} base={p.base_rate} P={p.probability:.3f} sub={p.submission}")
