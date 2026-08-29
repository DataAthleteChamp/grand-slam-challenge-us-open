import math, random
from scipy.optimize import brentq
from bot.data.oddsapi import devig_shin, devig_multiplicative, BookLine, consensus

def shin_reference(odds):
    implied = {k: 1.0/v for k, v in odds.items()}
    total = sum(implied.values())
    def p(z, pi):
        return (math.sqrt(z*z + 4*(1-z)*pi*pi/total) - z) / (2*(1-z))
    f = lambda z: sum(p(z, pi) for pi in implied.values()) - 1.0
    # true two-way closed form: z = ((n-1)/(n-2)) * ... use numeric
    lo, hi = 1e-12, 0.999999
    if f(lo)*f(hi) > 0:
        return None, None
    z = brentq(f, lo, hi, xtol=1e-14)
    return z, {k: p(z, pi) for k, pi in implied.items()}

random.seed(1)
worst = 0.0
for _ in range(4000):
    p_true = random.uniform(0.02, 0.98)
    margin = random.uniform(0.005, 0.12)
    # build overround-ed prices
    q1 = p_true * (1 + margin); q2 = (1 - p_true) * (1 + margin)
    odds = {"A": 1/q1, "B": 1/q2}
    got = devig_shin(odds)
    z, ref = shin_reference(odds)
    if ref is None:
        continue
    d = max(abs(got[k] - ref[k]) for k in ref)
    worst = max(worst, d)
print("max deviation from brentq reference over 4000 random two-way books: %.2e" % worst)

# adversarial
print("huge longshot:", devig_shin({"A": 1.01, "B": 51.0}))
print("massive vig  :", devig_shin({"A": 1.2, "B": 1.2}))
print("tiny vig     :", devig_shin({"A": 2.0, "B": 2.0}))
print("arb          :", devig_shin({"A": 2.1, "B": 2.1}))
print("zero price   :", end=" ")
try:
    print(devig_shin({"A": 0.0, "B": 2.0}))
except Exception as e:
    print("RAISED", type(e).__name__, e)
print("neg price    :", end=" ")
try:
    print(devig_shin({"A": -2.0, "B": 2.0}))
except Exception as e:
    print("RAISED", type(e).__name__, e)
print("one-way      :", devig_shin({"A": 1.5}))
print("extreme      :", devig_shin({"A": 1.001, "B": 1000.0}))
