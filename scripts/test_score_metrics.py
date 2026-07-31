"""Tests for the metric-testing harness.

Covers: decorator registration, the two placeholder metrics on hand-picked
inputs, and score_case's ranking behavior on a tempdir-built case.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from score_metrics import (
    METRICS,
    char_recall,
    register_metric,
    score_case,
    word_recall,
)


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------

def test_register_metric_adds_to_registry() -> None:
    name = "_test_tmp_metric"
    assert name not in METRICS

    @register_metric(name)
    def _m(ref: str, cand: str) -> float:
        return 0.5

    try:
        assert METRICS[name] is _m
        assert _m("a", "b") == 0.5
    finally:
        METRICS.pop(name, None)


def test_register_metric_rejects_duplicates() -> None:
    name = "_test_dup_metric"

    @register_metric(name)
    def _m(ref: str, cand: str) -> float:
        return 1.0

    try:
        with pytest.raises(ValueError):
            @register_metric(name)
            def _m2(ref: str, cand: str) -> float:
                return 0.0
    finally:
        METRICS.pop(name, None)


# ---------------------------------------------------------------------------
# Placeholder metrics.
# ---------------------------------------------------------------------------

def test_char_recall_identity_is_one() -> None:
    assert char_recall("hello", "hello") == 1.0


def test_char_recall_missing_char() -> None:
    assert char_recall("hello", "helo") == pytest.approx(0.8)


def test_char_recall_empty_reference() -> None:
    assert char_recall("", "") == 1.0
    assert char_recall("", "x") == 0.0


def test_word_recall_identity_is_one() -> None:
    assert word_recall("the quick brown fox", "the quick brown fox") == 1.0


def test_word_recall_partial() -> None:
    assert word_recall("the quick brown fox", "the quick brown") == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# score_case end-to-end on a hand-built tempdir case.
# ---------------------------------------------------------------------------

def _write_case(
    tmp_path: Path, clean: str, flawed: str, manifest: list[dict]
) -> Path:
    case = tmp_path / "case_x"
    case.mkdir()
    (case / "clean.txt").write_text(clean, encoding="utf-8")
    (case / "flawed.txt").write_text(flawed, encoding="utf-8")
    (case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return case


def test_score_case_flags_flawed_below_clean(tmp_path: Path) -> None:
    clean = "the quick brown fox jumps over the lazy dog"
    flawed = "the quick brown fox jumps over"
    manifest = [{
        "type": "missing_content",
        "start_offset": 30,
        "length": 13,
        "deleted_content": " the lazy dog",
    }]
    case_dir = _write_case(tmp_path, clean, flawed, manifest)

    result = score_case(case_dir)

    assert result["case_name"] == "case_x"
    assert result["manifest_summary"]["total_events"] == 1
    assert result["manifest_summary"]["type_counts"] == {"missing_content": 1}
    assert result["manifest_summary"]["chars_deleted"] == 13

    for name in ("char_recall", "word_recall"):
        m = result["metrics"][name]
        assert m["clean"] == 1.0
        assert m["flawed"] < m["clean"]
        assert m["flawed_ranked_below_clean"] is True
        assert m["delta"] < 0


def test_score_case_all_metrics_perfect_when_flawed_equals_clean(
    tmp_path: Path,
) -> None:
    clean = "identical text"
    case_dir = _write_case(tmp_path, clean, clean, [])
    result = score_case(case_dir)
    for name in ("char_recall", "word_recall"):
        m = result["metrics"][name]
        assert m["clean"] == 1.0
        assert m["flawed"] == 1.0
        assert m["flawed_ranked_below_clean"] is False


# ---------------------------------------------------------------------------
# Schema metrics (2026-07-29 slate). Validation rule: every metric must rank
# a known-flawed input below clean, and score clean-vs-clean perfectly.
# ---------------------------------------------------------------------------

from score_metrics import (  # noqa: E402
    score_ingredients,
    score_raw_coverage,
    score_reading,
    score_scalars,
    score_steps,
    split_numbered_steps,
    to_decimal,
    wer,
)

GT_FIXTURE = {
    "recipe_title": "Slow-Roasted Sunday Chicken",
    "cook_info": {"serves": "4-6"},
    "ingredients": [
        {"quantity": "1.5", "unit": "cups", "item": "orzo", "prep": "", "notes": ""},
        {"quantity": "0.5", "unit": "cup", "item": "dry white wine", "prep": "", "notes": ""},
        {"quantity": "3", "unit": "cloves", "item": "garlic", "prep": "finely chopped", "notes": ""},
        {"quantity": "", "unit": "", "item": "salt", "prep": "", "notes": "to taste"},
    ],
    "steps": [
        {"number": 1, "text": "Preheat the oven to 200°F."},
        {"number": 2, "text": "Pour in 1/2 cup wine and roast for 45 minutes."},
    ],
}

CLEAN_PARSE = {
    "ingredients": [
        {"amount": "1 1/2", "unit": "cups", "name": "orzo"},
        {"amount": "1/2", "unit": "cup", "name": "dry white wine"},
        {"amount": "3", "unit": "cloves", "name": "garlic, finely chopped"},
        {"amount": None, "unit": None, "name": "salt"},
    ],
    "instructions": "1. Preheat the oven to 200°F.\n\n2. Pour in 1/2 cup wine and roast for 45 minutes.",
    "title": "Slow-Roasted Sunday Chicken",
    "servings": 4,
}


def _score(parse: dict, raw_text: str = "irrelevant") -> dict:
    return score_reading(
        GT_FIXTURE,
        ingredients=parse["ingredients"],
        instructions=parse["instructions"],
        title=parse["title"],
        servings=parse["servings"],
        raw_text=raw_text,
        gt_flat_text="the flat page text for coverage",
    )


def test_to_decimal_forms() -> None:
    assert to_decimal("1 1/2") == 1.5
    assert to_decimal("½") == 0.5
    assert to_decimal("1½") == 1.5
    assert to_decimal("0.5") == 0.5
    assert to_decimal("3") == 3.0
    assert to_decimal("to taste") is None
    assert to_decimal(None) is None
    assert to_decimal("") is None


def test_clean_parse_scores_perfect() -> None:
    r = _score(CLEAN_PARSE)
    a, b, d = r["ingredients"], r["steps"], r["scalars"]
    assert a["recall"] == a["precision"] == 1.0
    assert a["quantity_acc"] == a["unit_acc"] == 1.0
    assert b["mean_wer"] == 0.0 and b["numeric_recall"] == 1.0
    assert d["title_sim"] == 1.0 and d["servings_ok"] is True


def test_glyph_corruption_sinks_quantity() -> None:
    flawed = [dict(i) for i in CLEAN_PARSE["ingredients"]]
    flawed[0] = dict(flawed[0], amount="1%")  # the classic ½ glyph destruction
    clean = score_ingredients(GT_FIXTURE["ingredients"], CLEAN_PARSE["ingredients"])
    bad = score_ingredients(GT_FIXTURE["ingredients"], flawed)
    assert bad["quantity_acc"] < clean["quantity_acc"]


def test_verbose_name_still_pairs() -> None:
    verbose = [{"amount": "3", "unit": None,
                "name": "garlic cloves, finely chopped or grated, plus 3 whole cloves"}]
    s = score_ingredients([GT_FIXTURE["ingredients"][2]], verbose)
    assert s["n_matched"] == 1  # containment boost: GT item "garlic" is in the name


def test_hallucinated_ingredient_sinks_precision_not_recall() -> None:
    padded = CLEAN_PARSE["ingredients"] + [{"amount": "9", "unit": "cups", "name": "motor oil"}]
    s = score_ingredients(GT_FIXTURE["ingredients"], padded)
    assert s["recall"] == 1.0
    assert s["precision"] < 1.0


def test_word_misread_sinks_wer() -> None:
    bad = dict(CLEAN_PARSE,
               instructions=CLEAN_PARSE["instructions"].replace("Preheat", "Preheal"))
    assert _score(bad)["steps"]["mean_wer"] > 0.0


def test_numeric_corruption_sinks_numeric_recall() -> None:
    bad = dict(CLEAN_PARSE,
               instructions=CLEAN_PARSE["instructions"].replace("200°F", "200°R"))
    r = _score(bad)["steps"]
    assert r["numeric_recall"] < 1.0


def test_missing_step_number_reported_as_hole() -> None:
    bad = dict(CLEAN_PARSE, instructions="1. Preheat the oven to 200°F.")
    r = _score(bad)["steps"]
    assert r["missing_numbers"] == [2]
    assert r["n_aligned"] == 1


def test_truncation_sinks_coverage_recall() -> None:
    full = score_raw_coverage("one two three four", "one two three four")
    cut = score_raw_coverage("one two three four", "one two")
    assert cut["word_recall"] < full["word_recall"]
    assert cut["word_precision"] == 1.0  # nothing hallucinated, just missing


def test_wrong_title_sinks_title_sim() -> None:
    good = score_scalars(GT_FIXTURE, "Slow-Roasted Sunday Chicken", 4)
    bad = score_scalars(GT_FIXTURE, "gundday chicken", 4)
    assert bad["title_sim"] < good["title_sim"]


def test_servings_range_membership() -> None:
    assert score_scalars(GT_FIXTURE, "t", 5)["servings_ok"] is True
    assert score_scalars(GT_FIXTURE, "t", 2)["servings_ok"] is False


def test_step_split_accepts_both_marker_styles() -> None:
    assert split_numbered_steps("1. a\n2.) b") == {("", 1): "a", ("", 2): "b"}


def test_wer_identity_and_scale() -> None:
    assert wer("a b c", "a b c") == 0.0
    assert wer("a b c", "a x c") == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# Multi-section / group / range extensions (rulings 2026-07-31).
# ---------------------------------------------------------------------------

from score_metrics import amount_values, quantity_values  # noqa: E402


def test_range_quantity_list_matches_both_endpoints() -> None:
    gt = [{"quantity": ["1", "2"], "unit": "teaspoons", "item": "pepper flakes"}]
    right = [{"amount": "1 to 2", "unit": "teaspoons", "name": "pepper flakes"}]
    half = [{"amount": "1", "unit": "teaspoons", "name": "pepper flakes"}]
    assert score_ingredients(gt, right)["quantity_acc"] == 1.0
    assert score_ingredients(gt, half)["quantity_acc"] == 0.0  # half-read the range


def test_quantity_values_forms() -> None:
    assert quantity_values(["1", "2"]) == [1.0, 2.0]
    assert quantity_values("1.5") == [1.5]
    assert quantity_values("") is None
    assert amount_values("1-2") == [1.0, 2.0]
    assert amount_values("1 1/2") == [1.5]


def test_same_item_in_two_groups_pairs_within_group() -> None:
    gt = [
        {"quantity": "2", "unit": "teaspoons", "item": "ground cumin", "group": "Main"},
        {"quantity": "1", "unit": "teaspoon", "item": "ground cumin", "group": "goddess sauce"},
    ]
    parsed = [
        {"amount": "2", "unit": "teaspoons", "name": "ground cumin", "group": "Main"},
        {"amount": "1", "unit": "teaspoon", "name": "ground cumin", "group": "goddess sauce"},
    ]
    s = score_ingredients(gt, parsed)
    # Cross-group pairing would score one quantity wrong; within-group both pass.
    assert s["n_matched"] == 2 and s["quantity_acc"] == 1.0


def test_sectioned_steps_do_not_collide() -> None:
    gt_steps = [
        {"number": 1, "text": "Roast the salmon."},
        {"number": 1, "text": "Blend the sauce.", "section": "goddess sauce"},
    ]
    instructions = "1. Roast the salmon.\n\n--- goddess sauce ---\n1. Blend the sauce."
    s = score_steps(gt_steps, instructions)
    assert s["n_aligned"] == 2 and s["mean_wer"] == 0.0
