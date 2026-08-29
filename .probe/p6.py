import json
from bot.markets.parser import parse_question
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

roster = Roster.load()
pricer = Pricer()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))
out = {'match_winner': [], 'tiebreak': [], 'aces_comparison': [], 'double_faults_comparison': []}
for entry in corpus['matches']:
    m = entry['match']
    a, b = players_from_match(m)
    ctx = roster.context(a, b)
    for mk in entry['markets']:
        q = mk['question']
        spec = parse_question(q, players=(ctx.player_a.name, ctx.player_b.name))
        p = pricer.price(spec, ctx, market_id=mk['id'])
        if spec.family.value in out:
            out[spec.family.value].append((ctx.tour, m['name'], q, spec.threshold, spec.comparator,
                                           p.model_probability, p.base_rate, p.probability, p.submission))
for fam in ('match_winner','tiebreak'):
    print("=====", fam)
    for r in out[fam]:
        print(f"{r[0]} {r[1][:32]:32s} | {r[2][:48]:48s} thr={r[3]} model={r[5]} base={r[6]:.3f} P={r[7]:.3f} sub={r[8]}")
