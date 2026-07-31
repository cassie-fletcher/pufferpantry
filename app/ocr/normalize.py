"""Shared ingredient normalization applied by every OCR reader.

Two rules, both designed and empirically validated against the owner's recipe
database. Pure functions, no I/O — same list in, same list out.

Rule 1 — split quantity-less conjunctions (`split_fused`)
---------------------------------------------------------
An entry splits into two iff BOTH:
  * its `amount` is None or empty, AND
  * its `name` contains " and " or " and/or " (case-insensitive, surrounded
    by spaces).
The split happens at the FIRST such conjunction. Both halves get amount=None,
unit=None and inherit `group`/`category`. Any trailing comma-separated clauses
and/or parenthetical after the right-hand item name distribute to BOTH sides
("chopped fresh basil and/or parsley, for serving (optional)" -> the
", for serving (optional)" tail applies to basil too). The left side keeps its
own leading descriptors.

Entries WITH an amount are NEVER split. This is measured policy, not caution:
25/25 quantified conjunctions in the owner's DB are prep language that must
not split (e.g. "avocado, halved and pitted", "lemon, zest and juice").

Known open edge, deliberately NOT implemented: quantified "and/or" entries
(e.g. "1/2 cup fresh cilantro and/or basil") stay fused pending an owner
convention on how the quantity should distribute.

Rule 2 — sum "plus N" compounds (`sum_compound`)
------------------------------------------------
For an entry whose name matches ", plus N X" / " plus N X" (N an int, decimal,
or ascii fraction): find the entry's COUNTING NOUN — its `unit` if set and
non-empty, otherwise the last word of the name segment before the first comma.
If X contains that noun (compared singular/plural-insensitively: strip a
trailing "s" from both before comparing), the "plus" quantity counts the same
thing as the main amount, so new amount = old amount + N ("3 garlic cloves,
..., plus 3 whole cloves" -> "6"). Otherwise — and always for "plus more ..."
with no number — the entry is unchanged. The name is never rewritten.
"""

from __future__ import annotations

import re
from fractions import Fraction

from app.schemas.recipe import IngredientCreate

# " and " or " and/or ", case-insensitive, surrounded by whitespace.
# "and/or" is listed first so it wins at a position where both could start
# (plain "and" cannot actually match there — the "/" is not a space — but the
# ordering makes the intent explicit).
_CONJUNCTION_RE = re.compile(r"\s(and/or|and)\s", re.IGNORECASE)

# A trailing parenthetical at the end of a conjunction's right-hand side,
# e.g. "parsley (optional)" — used only when there is no comma tail.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")

# ", plus N X" / " plus N X" where N is a mixed fraction ("1 1/2"), a bare
# fraction ("1/2"), a decimal ("1.5"), or an integer ("3"). "plus more ..."
# has no number and therefore never matches.
_PLUS_RE = re.compile(
    r"(?:,\s*|\s)plus\s+"
    r"(\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
    r"\s+(\S.*)$",
    re.IGNORECASE,
)


def _trailing_qualifier(right: str) -> str:
    """The tail of a right-hand side that distributes to both split halves.

    Everything from the first comma onward; failing that, a trailing
    parenthetical. Empty string when the right side is a bare item name.
    """
    comma = right.find(",")
    if comma != -1:
        return right[comma:]
    match = _TRAILING_PAREN_RE.search(right)
    if match and match.start() > 0:
        return " " + right[match.start() :].strip()
    return ""


def split_fused(ingredients: list[IngredientCreate]) -> list[IngredientCreate]:
    """Rule 1: split quantity-less " and " / " and/or " entries in two."""
    result: list[IngredientCreate] = []
    for ing in ingredients:
        if ing.amount is not None and ing.amount.strip():
            # Quantified entries NEVER split (measured: 25/25 are prep
            # language like "halved and pitted"). This also keeps quantified
            # "and/or" entries fused — the known open edge documented above.
            result.append(ing)
            continue
        match = _CONJUNCTION_RE.search(ing.name)
        if not match:
            result.append(ing)
            continue
        left = ing.name[: match.start()].strip()
        right = ing.name[match.end() :].strip()
        if not left or not right:
            result.append(ing)
            continue
        # Trailing comma-qualifiers / parenthetical distribute to both sides.
        left += _trailing_qualifier(right)
        for name in (left, right):
            result.append(
                IngredientCreate(
                    name=name,
                    amount=None,
                    unit=None,
                    order=ing.order,
                    group=ing.group,
                    category=ing.category,
                )
            )
    return result


def _parse_number(text: str) -> Fraction | None:
    """"1 1/2", "1/2", "1.5" or "3" -> Fraction. None if not one of those."""
    text = text.strip()
    match = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", text)
    if match:
        return Fraction(int(match[1])) + Fraction(int(match[2]), int(match[3]))
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return Fraction(int(match[1]), int(match[2]))
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return Fraction(text)  # Fraction accepts decimal strings exactly
    return None


def _format_amount(value: Fraction, as_decimal: bool) -> str:
    """Render a summed amount back to the string form `amount` requires."""
    if value.denominator == 1:
        return str(value.numerator)  # clean integer whenever whole
    if as_decimal:
        return f"{float(value):g}"
    whole, remainder = divmod(value.numerator, value.denominator)
    if whole:
        return f"{whole} {remainder}/{value.denominator}"
    return f"{value.numerator}/{value.denominator}"


def _singular(word: str) -> str:
    word = word.strip(".,;:()").lower()
    return word[:-1] if word.endswith("s") else word


def sum_compound(ing: IngredientCreate) -> IngredientCreate:
    """Rule 2: fold "plus N X" into the amount when X counts the same noun."""
    if ing.amount is None or not ing.amount.strip():
        return ing
    match = _PLUS_RE.search(ing.name)
    if not match:
        return ing

    # Counting noun: the unit when there is one, else the last word of the
    # name segment before the first comma ("garlic cloves, ..." -> "cloves").
    if ing.unit is not None and ing.unit.strip():
        counting_noun = _singular(ing.unit)
    else:
        head = ing.name.split(",", 1)[0].split()
        if not head:
            return ing
        counting_noun = _singular(head[-1])

    # X must contain the counting noun (singular/plural-insensitive) for the
    # "plus" quantity to count the same thing as the main amount.
    if counting_noun not in {_singular(word) for word in match[2].split()}:
        return ing

    base = _parse_number(ing.amount)
    extra = _parse_number(match[1])
    if base is None or extra is None:
        return ing

    as_decimal = "." in ing.amount or "." in match[1]
    return ing.model_copy(update={"amount": _format_amount(base + extra, as_decimal)})


def normalize_ingredients(
    ingredients: list[IngredientCreate],
) -> list[IngredientCreate]:
    """Apply both rules, then renumber `order` sequentially from 0."""
    normalized = [sum_compound(ing) for ing in split_fused(ingredients)]
    return [ing.model_copy(update={"order": i}) for i, ing in enumerate(normalized)]
