"""ROVER-style combination of independent readings (Cassie's design, 2026-08-01).

Purpose is VERIFICATION, not accuracy magic: with two voters every
disagreement is 1-vs-1, so the combiner's real products are per-field
confidence flags for the review UI. The voting rule is written for N voters
so adding one later is a config entry, not surgery:

    For each field: collect all voters' readings, canonicalize, keep only
    the LEGIBLE candidates (garble heuristics — no dictionary; one can be
    added later if needed), majority-vote among those, break ties by
    VOTER_PRIORITY order. Confidence = how much of the electorate agreed:
        high    all voters present and agreeing
        medium  only one voter saw it
        low     voters disagreed (the winner shipped anyway)

There is deliberately NO garbage-voter guard: a voter that disagrees with
everything (tesseract on the salmon pages) produces field flags on every
row — Cassie wants that visible for debugging, not summarized away.

Scope: per-ingredient fields get the full vote + flags (they have a UI home,
`amount_confidence`). Title / servings / instructions are voted as whole
fields (legibility first, then priority) — per-step flags have nowhere to
surface yet.
"""

from __future__ import annotations

import difflib
import re
from fractions import Fraction

from app.ocr.base import Reading
from app.schemas.recipe import IngredientCreate, PhotoExtractResult

# Highest priority first: used to pick the BASE reading (whose structure the
# output follows) and to break ties among legible candidates. Adding a voter
# later = adding its method name here.
VOTER_PRIORITY: tuple[str, ...] = ("claude", "apple_vision", "tesseract")

# Pairing: an ingredient in another voter's reading refers to the same thing
# as a base ingredient when, within the same group, the normalized names
# reach this similarity (or one name's tokens contain the other's).
PAIR_SIMILARITY = 0.6

# --------------------------------------------------------------------------
# Legibility — garble heuristics, per Cassie: no dictionary for now.
# --------------------------------------------------------------------------

# Characters that legitimately appear inside recipe tokens. Anything else
# inside a token ("oj]", "lY%", '"2') marks it garbled.
_TOKEN_OK_RE = re.compile(r"^[A-Za-zÀ-ÿ0-9'’/().,:;&°\-]+$")
# "%" is legal only as a percentage ("50%"); glued to letters ("lY%") it is
# the classic tesseract fraction-glyph mangle and marks the token garbled.
_PERCENT_OK_RE = re.compile(r"^\d+%$")
# A token that mixes letters and digits with no separator ("lY2", "0a73")
# is OCR debris; hyphen/slash-separated mixes ("4-pound", "1/2") are fine.
_LETTER_DIGIT_MIX_RE = re.compile(r"[A-Za-z][0-9]|[0-9][A-Za-z]")

#: A text is legible when at most this fraction of its tokens are garbled.
MAX_GARBLED_FRACTION = 0.2


def _token_garbled(token: str) -> bool:
    stripped = token.strip("\"'“”‘’.,;:!?()[]{}")
    if not stripped:
        return True
    if "%" in stripped:
        return not _PERCENT_OK_RE.match(stripped)
    if not _TOKEN_OK_RE.match(stripped):
        return True
    for part in re.split(r"[-/]", stripped):
        if _LETTER_DIGIT_MIX_RE.search(part):
            return True
    return False


def is_legible(text: str | None) -> bool:
    """True when the text reads as words rather than OCR debris.

    Catches symbol junk ("lY% Cups orzo", "olive oj]") but NOT letters-only
    garble ("gundday") — that class needs a dictionary, deliberately deferred.
    """
    if not text or not text.strip():
        return False
    tokens = text.split()
    if not any(any(ch.isalpha() for ch in t) for t in tokens):
        return False
    garbled = sum(1 for t in tokens if _token_garbled(t))
    return garbled / len(tokens) <= MAX_GARBLED_FRACTION


# --------------------------------------------------------------------------
# Canonical comparison keys
# --------------------------------------------------------------------------


def _canon_text(s: str | None) -> str:
    if not s:
        return ""
    tokens = (t.strip("\"'.,;:!?()[]{}") for t in s.lower().split())
    return " ".join(t for t in tokens if t)


_AMOUNT_PART_RE = re.compile(r"(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)")


def _amount_key(amount: str | None) -> tuple:
    """Decimals for an amount string, so '1 1/2', '1.5' and '3/2' agree.

    Ranges keep both endpoints. Non-numeric amounts fall back to text form.
    """
    if not amount:
        return ()
    values = []
    for part in _AMOUNT_PART_RE.findall(amount):
        try:
            if " " in part:
                whole, frac = part.split()
                values.append(float(int(whole) + Fraction(frac)))
            else:
                values.append(float(Fraction(part)))
        except (ValueError, ZeroDivisionError):
            continue
    return tuple(sorted(values)) if values else (_canon_text(amount),)


def _unit_key(unit: str | None) -> str:
    return _canon_text(unit)


# --------------------------------------------------------------------------
# Voting
# --------------------------------------------------------------------------


def _priority(method: str) -> int:
    base = method.split(":")[0]
    try:
        return VOTER_PRIORITY.index(base)
    except ValueError:
        return len(VOTER_PRIORITY)


def _vote(candidates: list[tuple[str, object, object]]) -> tuple[object, str]:
    """(winning value, confidence) from (voter, value, canonical_key) triples.

    Majority among legible/usable candidates; ties broken by VOTER_PRIORITY.
    Confidence: high = unanimous with 2+ voters, medium = single voter,
    low = any disagreement.
    """
    if not candidates:
        return None, "medium"
    keys = {}
    for voter, value, key in candidates:
        keys.setdefault(key, []).append((voter, value))
    if len(keys) == 1:
        (_, group), = keys.items()
        return group[0][1], ("high" if len(group) >= 2 else "medium")
    # Disagreement: largest bloc wins; priority breaks bloc-size ties.
    def bloc_rank(item):
        _, group = item
        best_priority = min(_priority(v) for v, _ in group)
        return (-len(group), best_priority)

    _, winners = sorted(keys.items(), key=bloc_rank)[0]
    winner = sorted(winners, key=lambda vv: _priority(vv[0]))[0][1]
    return winner, "low"


def _names_match(a: str, b: str) -> bool:
    ca, cb = _canon_text(a), _canon_text(b)
    if not ca or not cb:
        return False
    ta, tb = set(ca.split()), set(cb.split())
    if ta <= tb or tb <= ta:
        return True
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= PAIR_SIMILARITY


def _pair_to_base(
    base: list[IngredientCreate], other: list[IngredientCreate]
) -> tuple[dict[int, IngredientCreate], list[IngredientCreate]]:
    """Greedy one-to-one pairing of another voter's list onto the base list,
    within matching groups. Returns (base_index -> other entry, unpaired)."""
    paired: dict[int, IngredientCreate] = {}
    used: set[int] = set()
    scored = []
    for bi, b in enumerate(base):
        for oi, o in enumerate(other):
            if _canon_text(b.group or "Main") != _canon_text(o.group or "Main"):
                continue
            if _names_match(b.name, o.name):
                sim = difflib.SequenceMatcher(
                    None, _canon_text(b.name), _canon_text(o.name)
                ).ratio()
                scored.append((sim, bi, oi))
    for _, bi, oi in sorted(scored, key=lambda t: (-t[0], t[1], t[2])):
        if bi in paired or oi in used:
            continue
        paired[bi] = other[oi]
        used.add(oi)
    unpaired = [o for oi, o in enumerate(other) if oi not in used]
    return paired, unpaired


def _combine_ingredients(readings: list[Reading]) -> list[IngredientCreate]:
    base_reading, *others = readings
    base = list(base_reading.schema.ingredients)
    pairings = []  # per other voter: (method, base_index -> entry, unpaired)
    for r in others:
        paired, unpaired = _pair_to_base(base, list(r.schema.ingredients))
        pairings.append((r.method, paired, unpaired))

    combined: list[IngredientCreate] = []
    for bi, b in enumerate(base):
        amount_cands = []
        unit_cands = []
        if b.amount is not None or is_legible(b.name):
            amount_cands.append((base_reading.method, b.amount, _amount_key(b.amount)))
            unit_cands.append((base_reading.method, b.unit, _unit_key(b.unit)))
        for method, paired, _ in pairings:
            o = paired.get(bi)
            if o is None:
                continue
            amount_cands.append((method, o.amount, _amount_key(o.amount)))
            unit_cands.append((method, o.unit, _unit_key(o.unit)))
        amount, amount_conf = _vote(amount_cands)
        unit, unit_conf = _vote(unit_cands)
        # One flag per ingredient (the UI's field): the worse of the two.
        order = {"low": 0, "medium": 1, "high": 2}
        conf = min((amount_conf, unit_conf), key=lambda c: order[c])
        combined.append(
            b.model_copy(
                update={
                    "amount": amount if amount is not None else b.amount,
                    "unit": unit if unit is not None else b.unit,
                    "amount_confidence": conf,
                }
            )
        )

    # Entries only a non-base voter saw: kept when legible, flagged medium.
    for method, _, unpaired in pairings:
        for o in unpaired:
            if is_legible(o.name):
                combined.append(o.model_copy(update={"amount_confidence": "medium"}))

    # NO re-normalization: readers already ran normalize_ingredients, and
    # running it again re-fires the sum rule on already-summed names
    # (measured: garlic 6 -> 9). Only renumber.
    return [i.model_copy(update={"order": n}) for n, i in enumerate(combined)]


def _vote_whole_field(readings: list[Reading], getter) -> object:
    """Whole-field vote (title/servings/instructions): legible majority,
    priority tiebreak; falls back to the base voter's value."""
    candidates = []
    for r in readings:
        value = getter(r.schema)
        if value is None:
            continue
        if isinstance(value, str) and not is_legible(value):
            continue
        if isinstance(value, str):
            key = _canon_text(value)
        elif isinstance(value, list):
            key = tuple(value)  # lists (servings_range) are unhashable as-is
        else:
            key = value
        candidates.append((r.method, value, key))
    value, _ = _vote(candidates)
    return value if value is not None else getter(readings[0].schema)


def combine(readings: list[Reading]) -> Reading:
    """Combine N independent readings into one, with per-ingredient flags.

    `readings` in any order; the BASE (whose structure the output follows) is
    the highest-priority voter present. Deterministic.
    """
    if not readings:
        raise ValueError("combine() needs at least one reading")
    ordered = sorted(readings, key=lambda r: _priority(r.method))
    if len(ordered) == 1:
        return ordered[0]

    base = ordered[0].schema
    schema = base.model_copy(
        update={
            "title": _vote_whole_field(ordered, lambda s: s.title),
            "servings": _vote_whole_field(ordered, lambda s: s.servings),
            "servings_range": _vote_whole_field(
                ordered, lambda s: s.servings_range
            ),
            "instructions": _vote_whole_field(ordered, lambda s: s.instructions),
            "ingredients": _combine_ingredients(ordered),
        }
    )
    return Reading(
        method="rover:" + "+".join(r.method for r in ordered),
        schema=schema,
        raw_text=None,
    )
