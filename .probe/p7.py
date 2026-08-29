import json
from bot.markets.parser import parse_question, reset_unparsed
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match
from bot.validate import validate_batch

reset_unparsed()
roster = Roster.load(); pricer = Pricer()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))
by_match, rounds, priced = {}, {}, []
for entry in corpus['matches']:
    m = entry['match']; mid = m['id']
    a, b = players_from_match(m)
    ctx = roster.context(a, b, round_code=m.get('round'), tour=m.get('tour'))
    rows = []
    for mk in entry['markets']:
        spec = parse_question(mk['question'], players=(ctx.player_a.name, ctx.player_b.name))
        rows.append(pricer.price(spec, ctx, market_id=mk['id']))
    by_match[mid] = rows; rounds[mid] = ctx.round_code; priced += rows

rep = validate_batch(priced, questions_by_match=by_match, rounds_by_match=rounds)
print(rep.render())
print()
print("blocked:", rep.blocked)
from collections import Counter
print(Counter(q.submission for q in priced).most_common(10))
