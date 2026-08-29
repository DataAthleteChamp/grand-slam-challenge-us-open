from bot.models.point import match_shape
from bot.models.simulate import _SetSampler
g = _SetSampler().grid
print("sampler grid =", g)
def snap(p): return round(p/g)*g
pa, pb = 0.65, 0.63
print("snapped:", round(snap(pa),4), round(snap(pb),4))
print("exact at snapped :", match_shape(snap(pa), snap(pb), 5, True, 10).p_a_wins_match)
print("exact at true    :", match_shape(pa, pb, 5, True, 10).p_a_wins_match)
worst = (0, None)
import itertools
for i in range(0, 13):
    for j in range(0, 13):
        pa = 0.58 + i*0.005; pb = 0.58 + j*0.005
        e_true = match_shape(pa, pb, 5, True, 10).p_a_wins_match
        e_snap = match_shape(snap(pa), snap(pb), 5, True, 10).p_a_wins_match
        d = abs(e_true - e_snap)
        if d > worst[0]:
            worst = (round(d,4), (round(pa,4), round(pb,4), round(e_true,4), round(e_snap,4)))
print("worst quantisation error on match-winner (Bo5, 169 matchups):", worst)
