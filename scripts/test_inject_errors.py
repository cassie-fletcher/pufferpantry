"""Tests for inject_errors: determinism, count correctness, reversibility."""
from __future__ import annotations

from pathlib import Path

import pytest

from inject_errors import (
    RenderedText,
    _decimal_to_fraction_glyph,
    inject_char_misreads,
    inject_glyph_confusions,
    inject_layout_damage,
    inject_missing_content,
    render_ground_truth,
    reverse_manifest,
)

YAML_PATH = str(
    Path(__file__).parent / "ground_truth" / "20260404_204704_04f01b.yaml"
)


@pytest.fixture(scope="module")
def rendered() -> RenderedText:
    return render_ground_truth(YAML_PATH, layout="tesseract-like")


@pytest.fixture(scope="module")
def rendered_flat() -> RenderedText:
    return render_ground_truth(YAML_PATH, layout="flat")


@pytest.fixture(scope="module")
def clean_text(rendered: RenderedText) -> str:
    return rendered.text


@pytest.fixture(scope="module")
def clean_flat(rendered_flat: RenderedText) -> str:
    return rendered_flat.text


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_char_misreads_deterministic(clean_text: str) -> None:
    a = inject_char_misreads(clean_text, 5, seed=42)
    b = inject_char_misreads(clean_text, 5, seed=42)
    assert a == b


def test_glyph_confusions_deterministic(clean_text: str) -> None:
    a = inject_glyph_confusions(clean_text, 2, seed=7)
    b = inject_glyph_confusions(clean_text, 2, seed=7)
    assert a == b


def test_layout_damage_deterministic(rendered: RenderedText) -> None:
    a = inject_layout_damage(rendered, 2, seed=42)
    b = inject_layout_damage(rendered, 2, seed=42)
    assert a == b


def test_missing_content_deterministic(clean_text: str) -> None:
    a = inject_missing_content(clean_text, 60, seed=42)
    b = inject_missing_content(clean_text, 60, seed=42)
    assert a == b


def test_different_seed_differs(clean_text: str) -> None:
    a, _ = inject_char_misreads(clean_text, 5, seed=1)
    b, _ = inject_char_misreads(clean_text, 5, seed=2)
    assert a != b


# ---------------------------------------------------------------------------
# Count correctness
# ---------------------------------------------------------------------------

def test_char_misreads_count(clean_text: str) -> None:
    _, manifest = inject_char_misreads(clean_text, 5, seed=42)
    assert len(manifest) == 5
    assert all(m["type"] == "char_misread" for m in manifest)


def test_glyph_confusions_count(clean_text: str) -> None:
    _, manifest = inject_glyph_confusions(clean_text, 2, seed=42)
    assert len(manifest) == 2
    assert all(m["type"] == "glyph_confusion" for m in manifest)


def test_layout_damage_count(rendered: RenderedText) -> None:
    _, manifest = inject_layout_damage(rendered, 3, seed=42)
    assert len(manifest) == 3
    assert all(m["type"] == "interleave" for m in manifest)


def test_missing_content_count(clean_text: str) -> None:
    _, manifest = inject_missing_content(clean_text, 60, seed=42)
    assert len(manifest) == 1
    assert manifest[0]["type"] == "missing_content"
    assert manifest[0]["length"] == 60


# ---------------------------------------------------------------------------
# Reversibility
# ---------------------------------------------------------------------------

def test_reverse_char_misreads(clean_text: str) -> None:
    flawed, manifest = inject_char_misreads(clean_text, 5, seed=42)
    assert flawed != clean_text
    assert reverse_manifest(flawed, manifest) == clean_text


def test_reverse_glyph_confusions(clean_text: str) -> None:
    flawed, manifest = inject_glyph_confusions(clean_text, 2, seed=42)
    assert flawed != clean_text
    assert reverse_manifest(flawed, manifest) == clean_text


def test_reverse_layout_damage(rendered: RenderedText) -> None:
    flawed, manifest = inject_layout_damage(rendered, 2, seed=42)
    assert flawed != rendered.text
    assert reverse_manifest(flawed, manifest) == rendered.text


def test_reverse_missing_content(clean_text: str) -> None:
    flawed, manifest = inject_missing_content(clean_text, 60, seed=42)
    assert flawed != clean_text
    assert reverse_manifest(flawed, manifest) == clean_text


def test_reverse_on_flat_layout(rendered_flat: RenderedText) -> None:
    flawed, manifest = inject_layout_damage(rendered_flat, 2, seed=5)
    assert reverse_manifest(flawed, manifest) == rendered_flat.text


# ---------------------------------------------------------------------------
# Rendering sanity
# ---------------------------------------------------------------------------

def test_tesseract_like_has_two_column_rows(clean_text: str) -> None:
    """Two-column ingredient rows should have content past LEFT_COL_WIDTH."""
    from inject_errors import LEFT_COL_WIDTH
    lines = clean_text.split("\n")
    wide = [ln for ln in lines if len(ln) > LEFT_COL_WIDTH]
    assert wide, "expected at least one two-column ingredient row"


def test_decimal_to_fraction_glyph() -> None:
    assert _decimal_to_fraction_glyph("0.5") == "½"
    assert _decimal_to_fraction_glyph("1.5") == "1½"
    assert _decimal_to_fraction_glyph("0.25") == "¼"
    assert _decimal_to_fraction_glyph("1.25") == "1¼"
    assert _decimal_to_fraction_glyph("0.75") == "¾"
    assert _decimal_to_fraction_glyph("1") == "1"
    assert _decimal_to_fraction_glyph("0.33") == "0.33"
    assert _decimal_to_fraction_glyph("") == ""
    assert _decimal_to_fraction_glyph("to taste") == "to taste"


def test_section_map_covers_expected_sections(rendered: RenderedText) -> None:
    """The renderer should populate title/headnote/cook_info/ingredients/steps."""
    for name in ("title", "headnote", "cook_info", "ingredients", "steps"):
        assert rendered.sections.get(name), f"section missing: {name}"


def test_headnote_placeholder_when_missing(tmp_path: Path) -> None:
    import yaml as _yaml
    src = _yaml.safe_load(Path(YAML_PATH).read_text())
    src["headnote"] = ""
    p = tmp_path / "empty_headnote.yaml"
    p.write_text(_yaml.safe_dump(src))
    out = render_ground_truth(str(p), layout="flat")
    assert "<HEADNOTE>" in out.text
