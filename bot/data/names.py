"""Player name normalisation across data sources.

Four sources, four naming conventions, and no shared identifier:

============  ==========================  =========================
source        format                      example
============  ==========================  =========================
Sackmann      ``First Last``              ``Carlos Alcaraz``
tennis-data   ``Last F.``                 ``Alcaraz C.``
Charting      ``First Last``              ``Carlos Alcaraz``
point-by-point ``First Last`` (slam feed)  ``Carlos Alcaraz``
============  ==========================  =========================

Joining on raw strings silently drops matches, and a silent drop is worse than a
crash: the player simply looks like they have less data, their rate gets shrunk
harder toward the prior, and nothing in the output says anything went wrong.

The approach here is to reduce every name to a comparable key — strip accents,
lowercase, drop punctuation — and to match ``Last F.`` forms by surname plus
first initial.  Ambiguous initials are reported rather than guessed, because a
wrong join is far more damaging than a missing one.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

#: Particles that belong to the surname rather than being given names.
_SURNAME_PARTICLES = {
    "de",
    "del",
    "della",
    "di",
    "da",
    "dos",
    "du",
    "van",
    "von",
    "der",
    "den",
    "ter",
    "la",
    "le",
    "el",
    "al",
    "bin",
    "ibn",
    "mc",
    "mac",
    "st",
}

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

#: ``Last F.`` / ``Last F.G.`` as used by tennis-data.co.uk.
_ABBREVIATED = re.compile(r"^(?P<last>.+?)\s+(?P<initials>(?:[A-Za-z]\.){1,3})$")


def strip_accents(text: str) -> str:
    """Remove diacritics so ``Swiatek`` and ``Świątek`` compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise(name: str) -> str:
    """Reduce a name to a lowercase, accent-free, punctuation-free string."""
    if not isinstance(name, str):
        return ""
    text = strip_accents(name).lower()
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def split_name(name: str) -> tuple[str, str]:
    """Split into ``(surname, given names)``, handling abbreviated forms.

    Returns the surname first because it is the only part every source agrees on.
    """
    raw = (name or "").strip()
    abbreviated = _ABBREVIATED.match(raw)
    if abbreviated:
        surname = normalise(abbreviated.group("last"))
        initials = normalise(abbreviated.group("initials")).replace(" ", "")
        return surname, initials

    tokens = normalise(raw).split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""

    # Walk backwards while the token is a surname particle, so "Van De Zandschulp"
    # and "De Minaur" keep their full surname.
    index = len(tokens) - 1
    while index > 0 and tokens[index - 1] in _SURNAME_PARTICLES:
        index -= 1
    return " ".join(tokens[index:]), " ".join(tokens[:index])


def candidate_keys(name: str) -> list[str]:
    """All plausible join keys for a canonical ``First Last`` name.

    Where the surname begins is genuinely ambiguous in a full name: "Roberto
    Bautista Agut" has a two-word surname, "Felix Auger Aliassime" has a
    hyphenated one that normalises to two words, and "Carlos Alcaraz" has one.
    Rather than guess, index the name under every reasonable split — taking the
    last one, two, and three tokens as the surname — so an abbreviated form can
    find it whichever convention the other source used.

    This is the fix for a 74% join rate: matching only on the final token loses
    every player with a compound surname, which is a large and non-random slice
    of the tour.
    """
    tokens = normalise(name).split()
    if not tokens:
        return []
    if len(tokens) == 1:
        return [f"{tokens[0]}|"]

    keys = []
    initial = tokens[0][0]
    for surname_length in (1, 2, 3):
        if surname_length >= len(tokens):
            break
        surname = " ".join(tokens[-surname_length:])
        keys.append(f"{surname}|{initial}")

    # Also index the particle-aware split, which handles "de Minaur" and
    # "van de Zandschulp" where the particle is not the first surname token.
    surname, given = split_name(name)
    if surname:
        keys.append(f"{surname}|{given[0] if given else ''}")
    return list(dict.fromkeys(keys))


def match_key(name: str) -> str:
    """A join key comparable across sources: ``surname|first-initial``.

    Deliberately coarse.  Surname plus one initial is what the abbreviated
    sources give us, so it is the most information every source shares.
    """
    surname, given = split_name(name)
    if not surname:
        return ""
    initial = given[0] if given else ""
    return f"{surname}|{initial}"


def build_crosswalk(canonical_names: list[str]) -> dict[str, str]:
    """Map join keys to canonical full names.

    Each canonical name is indexed under every candidate key, so an abbreviated
    ``Bautista Agut R.`` finds ``Roberto Bautista Agut`` regardless of where the
    surname was assumed to start.

    Keys that resolve to more than one canonical name are dropped rather than
    guessed — an ambiguous key is exactly the case where a wrong join does real
    damage.  Use :func:`ambiguous_keys` to inspect what was discarded.
    """
    grouped: dict[str, set[str]] = defaultdict(set)
    for name in canonical_names:
        for key in candidate_keys(name):
            grouped[key].add(name)
    return {key: next(iter(names)) for key, names in grouped.items() if len(names) == 1}


def ambiguous_keys(canonical_names: list[str]) -> dict[str, list[str]]:
    """Join keys that map to several canonical names, for manual review."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for name in canonical_names:
        for key in candidate_keys(name):
            grouped[key].add(name)
    return {key: sorted(names) for key, names in grouped.items() if len(names) > 1}


def resolve(name: str, crosswalk: dict[str, str]) -> str | None:
    """Resolve a source-specific name to its canonical form, or ``None``."""
    return crosswalk.get(match_key(name))


def join_report(source_names: list[str], crosswalk: dict[str, str]) -> dict[str, object]:
    """Summarise how well a source joins, and list what failed.

    Always look at ``unmatched`` before trusting a merge.  A join that quietly
    loses 20% of rows produces a dataset that looks fine and is not.
    """
    unmatched = sorted({n for n in source_names if n and resolve(n, crosswalk) is None})
    total = len({n for n in source_names if n})
    return {
        "total": total,
        "matched": total - len(unmatched),
        "match_rate": (total - len(unmatched)) / total if total else 0.0,
        "unmatched": unmatched,
    }
