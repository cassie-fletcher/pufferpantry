"""Tests for the ROVER combiner (verification via agreement, N-voter rule)."""
from __future__ import annotations

from app.ocr.base import Reading
from app.ocr.combine import combine, is_legible
from app.schemas.recipe import IngredientCreate, PhotoExtractResult


def reading(method, title="Sunday Chicken", ingredients=(), servings=4):
    return Reading(
        method=method,
        schema=PhotoExtractResult(
            title=title,
            servings=servings,
            instructions="1. Cook it.",
            ingredients=list(ingredients),
            photo_filename="x.jpg",
        ),
        raw_text=None,
    )


def ing(name, amount=None, unit=None, group="Main"):
    return IngredientCreate(name=name, amount=amount, unit=unit, group=group)


def test_legibility_heuristics():
    assert is_legible("2 cups baby spinach")
    assert is_legible("jalapeño, halved and seeded")
    assert not is_legible("lY% Cups orzo")
    # a mostly-readable line with one mangled token is LEGIBLE — the bad
    # amount is the amount-vote's problem, not legibility's
    assert is_legible('"2 Cup dry white wine, such as pinot Zrigio')
    assert not is_legible('oj] eee %] "2')
    # letters-only garble passes — known limit, dictionary deferred
    assert is_legible("gundday chicken")


def test_agreement_is_high_confidence():
    v = reading("apple_vision", ingredients=[ing("orzo", "1 1/2", "cups")])
    t = reading("tesseract", ingredients=[ing("orzo", "1.5", "cups")])  # canonical match
    out = combine([v, t])
    assert out.schema.ingredients[0].amount_confidence == "high"
    assert out.schema.ingredients[0].amount == "1 1/2"  # vision's form kept


def test_disagreement_is_low_and_vision_wins():
    v = reading("apple_vision", ingredients=[ing("dry white wine", "1/2", "cup")])
    t = reading("tesseract", ingredients=[ing("dry white wine", "7", "cup")])
    out = combine([v, t])
    i = out.schema.ingredients[0]
    assert i.amount_confidence == "low"
    assert i.amount == "1/2"


def test_single_voter_entry_is_medium():
    v = reading("apple_vision", ingredients=[ing("orzo", "1 1/2", "cups")])
    t = reading(
        "tesseract",
        ingredients=[ing("orzo", "1 1/2", "cups"), ing("balsamic vinegar", "2", "tablespoons")],
    )
    out = combine([v, t])
    by_name = {i.name: i for i in out.schema.ingredients}
    assert by_name["balsamic vinegar"].amount_confidence == "medium"
    assert by_name["orzo"].amount_confidence == "high"


def test_garbled_extra_entry_is_dropped():
    v = reading("apple_vision", ingredients=[ing("orzo", "1 1/2", "cups")])
    t = reading("tesseract", ingredients=[ing("orzo", "1 1/2", "cups"), ing("EE Oe %]")])
    out = combine([v, t])
    assert all("EE Oe" not in i.name for i in out.schema.ingredients)


def test_three_voters_majority_beats_priority():
    v = reading("apple_vision", ingredients=[ing("wine", "7", "cup")])
    t = reading("tesseract", ingredients=[ing("wine", "1/2", "cup")])
    c = reading("claude", ingredients=[ing("wine", "0.5", "cup")])
    out = combine([v, t, c])
    i = out.schema.ingredients[0]
    # two voters agree on one half; vision's 7 loses despite... claude being base
    assert i.amount in ("1/2", "0.5")
    assert i.amount_confidence == "low"  # there WAS disagreement


def test_illegible_title_loses_to_legible():
    v = reading("apple_vision", title="Slow-Roasted Sunday Chicken")
    t = reading("tesseract", title='J0w TOasteg %]')
    out = combine([t, v])
    assert out.schema.title == "Slow-Roasted Sunday Chicken"


def test_deterministic_and_order_independent():
    v = reading("apple_vision", ingredients=[ing("orzo", "1 1/2", "cups")])
    t = reading("tesseract", ingredients=[ing("orzo", "1", "cup")])
    a = combine([v, t])
    b = combine([t, v])
    assert a.schema == b.schema
    assert combine([v, t]).schema == combine([v, t]).schema


def test_method_name_records_voters():
    v = reading("apple_vision")
    t = reading("tesseract")
    assert combine([v, t]).method == "rover:apple_vision+tesseract"
