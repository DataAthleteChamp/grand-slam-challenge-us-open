"""Isolate the height-prior asymmetry in _count_comparison (aces)."""
import json
from bot.data.baserates import count_stats
from bot.models.counts import skellam_comparison
from bot.models.shrink import CREDIBILITY_K, height_prior_ace_rate, shrink
from bot.pricer import Pricer
from bot.roster import Roster, players_from_match

roster = Roster.load()
corpus = json.load(open('data/corpus/corpus-20260829T155009Z.json'))

def current(subject, opponent, ctx):
    """Exactly what Pricer._count_comparison does for aces."""
    prior = height_prior_ace_rate(subject.height, ctx.tour)   # <-- subject's height for BOTH
    k = CREDIBILITY_K["ace_rate"]
    s = shrink(subject.ace_rate, subject.matches, prior, k).estimate
    o = shrink(opponent.ace_rate, opponent.matches, prior, k).estimate
    n = ctx.expected_service_points
    return skellam_comparison(s*n, o*n)

def correct(subject, opponent, ctx):
    """Each player shrunk toward their own height prior."""
    k = CREDIBILITY_K["ace_rate"]
    s = shrink(subject.ace_rate, subject.matches,
               height_prior_ace_rate(subject.height, ctx.tour), k).estimate
    o = shrink(opponent.ace_rate, opponent.matches,
               height_prior_ace_rate(opponent.height, ctx.tour), k).estimate
    n = ctx.expected_service_points
    return skellam_comparison(s*n, o*n)

rows = []
for entry in corpus['matches']:
    a, b = players_from_match(entry['match'])
    ctx = roster.context(a, b)
    pa, pb = ctx.player_a, ctx.player_b
    if None in (pa.ace_rate, pb.ace_rate) or not (pa.matches and pb.matches):
        continue
    cur_ab = current(pa, pb, ctx).resolve('no')
    cur_ba = current(pb, pa, ctx).resolve('no')
    cor_ab = correct(pa, pb, ctx).resolve('no')
    cor_ba = correct(pb, pa, ctx).resolve('no')
    # internal inconsistency: the two orientations disagree about P(tie) too
    rows.append((abs(cur_ab - cor_ab), entry['match']['name'], ctx.tour,
                 pa.name, pa.height, pa.matches, pb.name, pb.height, pb.matches,
                 cur_ab, cor_ab, cur_ba, cor_ba))

rows.sort(reverse=True)
print(f"{'match':38s} {'hA':>5s} {'nA':>4s} {'hB':>5s} {'nB':>4s} {'cur A>B':>8s} {'fix A>B':>8s} {'delta':>7s}")
for r in rows[:15]:
    print(f"{r[1][:38]:38s} {r[4]!s:>5s} {r[5]:4d} {r[7]!s:>5s} {r[8]:4d} "
          f"{r[9]:8.3f} {r[10]:8.3f} {r[9]-r[10]:+7.3f}")
print()
print("max |delta| = %.4f over %d matches" % (rows[0][0], len(rows)))
print("mean |delta| = %.4f" % (sum(r[0] for r in rows)/len(rows)))
