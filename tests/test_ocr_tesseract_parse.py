"""Tests for tesseract's ingredient-run extraction (ported from resolve.py)."""
from __future__ import annotations

from app.ocr.tesseract import extract_ingredients


REGION = [
    "gundday chicken",                      # junk before the run — ignored
    "PREP 15 MINUTES",                      # cook-info — before run anyway
    "3 cloves garlic, finely chopped",
    "1 tablespoon fresh thyme leaves",
    "Fine pink Himalayan salt and freshly ground pepper",  # quantity-less, mid-run
    "2 cups baby spinach",
    "Chopped basil and/or parsley, for serving",  # quantity-less after quantity-led
]


def test_quantityless_line_inside_run_is_admitted() -> None:
    names = [i.name for i in extract_ingredients(REGION)]
    # the normalizer splits the conjunctions, so match on substrings
    assert any("salt" in n for n in names)
    assert any("pepper" in n for n in names)
    assert any("basil" in n for n in names)


def test_junk_before_run_is_ignored() -> None:
    names = [i.name for i in extract_ingredients(REGION)]
    assert not any("gundday" in n for n in names)


def test_all_caps_header_rejected_even_adjacent() -> None:
    region = ["3 cloves garlic", "TOTAL 3 HOURS 15 MINUTES", "2 cups spinach"]
    names = [i.name for i in extract_ingredients(region)]
    assert not any("HOURS" in n for n in names)


def test_prose_ending_line_not_admitted() -> None:
    region = ["3 cloves garlic", "Rub the oil all over the chicken."]
    names = [i.name for i in extract_ingredients(region)]
    assert len(names) == 1  # prose line ends the run


def test_two_quantityless_in_a_row_ends_run() -> None:
    region = [
        "3 cloves garlic",
        "Fine pink Himalayan salt",
        "freshly ground pepper on top of that",  # second quantity-less in a row
    ]
    names = [i.name for i in extract_ingredients(region)]
    assert len(names) == 2  # garlic + salt; the run ended before line 3
