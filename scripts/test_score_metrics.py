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
