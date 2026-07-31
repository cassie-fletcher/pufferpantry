"""Tests for app/ocr/normalize.py — the two shared ingredient rules.

Rule 1: split quantity-less " and " / " and/or " conjunctions.
Rule 2: sum "plus N X" compounds when X counts the same noun as the amount.
"""

from app.ocr.normalize import normalize_ingredients, split_fused, sum_compound
from app.schemas.recipe import IngredientCreate


def ing(name, amount=None, unit=None, order=0, group="Main", category=None):
    return IngredientCreate(
        name=name, amount=amount, unit=unit, order=order, group=group, category=category
    )


# --------------------------------------------------------------------------
# Rule 1 — splitting
# --------------------------------------------------------------------------


def test_salt_and_pepper_splits():
    result = split_fused([ing("Fine pink Himalayan salt and freshly ground pepper")])
    assert [i.name for i in result] == [
        "Fine pink Himalayan salt",
        "freshly ground pepper",
    ]
    assert all(i.amount is None and i.unit is None for i in result)


def test_split_with_tail_distribution():
    # The ", for serving (optional)" tail after the right-hand item name
    # distributes to BOTH sides; the left keeps its leading descriptors.
    result = split_fused(
        [ing("chopped fresh basil and/or parsley, for serving (optional)")]
    )
    assert [i.name for i in result] == [
        "chopped fresh basil, for serving (optional)",
        "parsley, for serving (optional)",
    ]


def test_split_inherits_group_and_category():
    result = split_fused(
        [ing("salt and pepper", group="Seasoning", category="spice")]
    )
    assert len(result) == 2
    assert all(i.group == "Seasoning" and i.category == "spice" for i in result)


def test_quantified_entries_never_split():
    # Measured policy: 25/25 quantified conjunctions in the owner's DB are
    # prep language, not two ingredients.
    entries = [
        ing("avocado, halved and pitted", amount="1"),
        ing("lemon, zest and juice", amount="1"),
        # Quantified "and/or" is the known open edge: stays fused pending an
        # owner convention on distributing the quantity.
        ing("fresh cilantro and/or basil", amount="1/2", unit="cup"),
        ing("romaine and baby arugula", amount="4", unit="cups"),
    ]
    result = split_fused(entries)
    assert result == entries


def test_half_and_half_quantity_less_splits_known_behavior():
    # "half and half" contains " and " so a quantity-less occurrence DOES
    # split under this rule. Documented known behavior, not a bug to fix:
    # the owner's data shows quantity-less "half and half" does not occur,
    # and quantified occurrences are protected by the amount guard.
    result = split_fused([ing("half and half")])
    assert [i.name for i in result] == ["half", "half"]
    # The quantified form stays fused.
    assert split_fused([ing("half and half", amount="1", unit="cup")]) == [
        ing("half and half", amount="1", unit="cup")
    ]


def test_split_only_at_first_conjunction():
    result = split_fused([ing("salt and pepper and paprika")])
    assert [i.name for i in result] == ["salt", "pepper and paprika"]


# --------------------------------------------------------------------------
# Rule 2 — summing "plus N" compounds
# --------------------------------------------------------------------------


def test_sum_garlic_cloves():
    # Counting noun "cloves" comes from the name-before-comma segment
    # (unit is None); "whole cloves" contains it -> 3 + 3 = 6.
    before = ing(
        "garlic cloves, finely chopped or grated, plus 3 whole cloves", amount="3"
    )
    after = sum_compound(before)
    assert after.amount == "6"
    assert after.name == before.name
    assert after.unit is None


def test_sum_thyme_sprig_unchanged():
    # Counting noun "tablespoon" (the unit) is not in "thyme sprig".
    before = ing("fresh thyme leaves, plus 1 thyme sprig", amount="1", unit="tablespoon")
    assert sum_compound(before) == before


def test_sum_plus_more_unchanged():
    # "plus more ..." has no number and never changes anything.
    before = ing("chopped fresh dill, plus more for serving", amount="1", unit="tablespoon")
    assert sum_compound(before) == before


def test_sum_rosemary_sprig_unchanged():
    # "teaspoons" vs "rosemary sprig" — different counting noun.
    before = ing(
        "chopped fresh rosemary, plus 1 rosemary sprig", amount="2", unit="teaspoons"
    )
    assert sum_compound(before) == before


# --------------------------------------------------------------------------
# normalize_ingredients — both rules + renumbering
# --------------------------------------------------------------------------


def test_normalize_renumbers_order_sequentially():
    result = normalize_ingredients(
        [
            ing("olive oil", amount="2", unit="tablespoons", order=0),
            ing("Fine pink Himalayan salt and freshly ground pepper", order=1),
            ing(
                "garlic cloves, finely chopped or grated, plus 3 whole cloves",
                amount="3",
                order=2,
            ),
        ]
    )
    assert [i.order for i in result] == [0, 1, 2, 3]
    assert [i.name for i in result] == [
        "olive oil",
        "Fine pink Himalayan salt",
        "freshly ground pepper",
        "garlic cloves, finely chopped or grated, plus 3 whole cloves",
    ]
    assert result[3].amount == "6"


def test_normalize_empty_list():
    assert normalize_ingredients([]) == []


def test_normalize_deterministic():
    entries = [
        ing("Fine pink Himalayan salt and freshly ground pepper", order=0),
        ing(
            "garlic cloves, finely chopped or grated, plus 3 whole cloves",
            amount="3",
            order=1,
        ),
        ing("avocado, halved and pitted", amount="1", order=2),
    ]
    snapshot = [e.model_copy(deep=True) for e in entries]
    first = normalize_ingredients(entries)
    second = normalize_ingredients(entries)
    assert first == second
    # Pure: the input list is not mutated.
    assert entries == snapshot
