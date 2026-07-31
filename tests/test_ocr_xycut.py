"""Tests for the xycut skew gate and resolver-block emission.

Two tiers:
  - Fast, pure-geometry tests: synthetic word boxes and hand-built cut
    trees. No Tesseract, no photos.
  - Real-photo tests (marked by `slow_photo`): run Tesseract's word pass on
    the chicken reference photo. Skipped when the photo is absent or when
    PUFFERPANTRY_SKIP_SLOW_OCR=1 (each word pass costs ~10s).
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest
from PIL import Image

from app.ocr.xycut import (
    MIN_SKEW_LINES,
    SKEW_GATE_DEG,
    Node,
    Word,
    assemble_block_text,
    block_nodes,
    deskew,
    get_words,
    line_skew_angles,
    maybe_deskew,
    measure_skew_deg,
)

CHICKEN_PHOTO = (
    Path(__file__).resolve().parents[1] / "data" / "photos" / "20260404_204704_04f01b.jpg"
)

slow_photo = pytest.mark.skipif(
    not CHICKEN_PHOTO.is_file() or os.environ.get("PUFFERPANTRY_SKIP_SLOW_OCR") == "1",
    reason="slow real-photo OCR test (needs the chicken reference photo; "
    "set PUFFERPANTRY_SKIP_SLOW_OCR=1 to skip)",
)


# --------------------------------------------------------------------------
# Synthetic word geometry
# --------------------------------------------------------------------------


def make_lines(
    n_lines: int,
    words_per_line: int,
    angle_deg: float,
    *,
    line_gap: int = 60,
    word_step: int = 90,
    word_w: int = 70,
    word_h: int = 20,
) -> list[Word]:
    """Word boxes laid out on parallel lines tilted by `angle_deg`.

    Positive angle = downhill left-to-right in image coords (y down),
    matching line_skew_angles' convention.
    """
    slope = math.tan(math.radians(angle_deg))
    words: list[Word] = []
    for li in range(n_lines):
        for wi in range(words_per_line):
            x0 = 50 + wi * word_step
            y_center = 100 + li * line_gap + slope * (x0 + word_w / 2)
            y0 = round(y_center - word_h / 2)
            words.append(
                Word(x0, y0, x0 + word_w, y0 + word_h, f"w{li}_{wi}", (1, 1, li))
            )
    return words


class TestSkewMeasurement:
    def test_flat_lines_measure_zero(self):
        words = make_lines(8, 6, 0.0)
        assert measure_skew_deg(words) == pytest.approx(0.0, abs=0.05)

    @pytest.mark.parametrize("angle", [2.0, -2.0, 5.0, -0.7])
    def test_tilted_lines_recover_angle(self, angle):
        words = make_lines(8, 6, angle)
        measured = measure_skew_deg(words)
        # Boxes are rounded to integer pixels; over a ~500 px span that
        # costs well under a tenth of a degree.
        assert measured == pytest.approx(angle, abs=0.1)

    def test_too_few_lines_returns_none(self):
        words = make_lines(MIN_SKEW_LINES - 1, 6, 3.0)
        assert measure_skew_deg(words) is None

    def test_short_lines_are_excluded(self):
        # 3-word lines measured +-6 deg outliers on real pages; the fit
        # requires MIN_SKEW_LINE_WORDS (4). Here every line is too short.
        words = make_lines(8, 3, 2.0)
        assert line_skew_angles(words) == []
        assert measure_skew_deg(words) is None

    def test_vertical_stack_does_not_crash(self):
        # All words at the same x: sxx == 0, no baseline to fit.
        words = [
            Word(50, 100 + 30 * i, 120, 120 + 30 * i, f"w{i}", (1, 1, 1))
            for i in range(6)
        ]
        assert line_skew_angles(words) == []


class TestGate:
    def test_below_gate_image_untouched(self):
        img = Image.new("RGB", (400, 300), (255, 255, 255))
        words = make_lines(8, 6, 0.3)
        out, angle, fired = maybe_deskew(img, words, SKEW_GATE_DEG)
        assert out is img  # the exact same object — bit-identical behavior
        assert not fired
        assert angle == pytest.approx(0.3, abs=0.1)

    def test_above_gate_rotates(self):
        img = Image.new("RGB", (400, 300), (255, 255, 255))
        words = make_lines(8, 6, 4.0)
        out, angle, fired = maybe_deskew(img, words, SKEW_GATE_DEG)
        assert fired
        assert out is not img
        assert out.size != img.size  # expand=True grows a rotated canvas
        assert angle == pytest.approx(4.0, abs=0.1)

    def test_unmeasurable_page_untouched(self):
        img = Image.new("RGB", (400, 300), (255, 255, 255))
        out, angle, fired = maybe_deskew(img, [], SKEW_GATE_DEG)
        assert out is img
        assert angle is None
        assert not fired

    def test_deskew_levels_synthetic_measurement(self):
        # Rotating the word centers by MINUS the measured angle (image
        # coords, y down) must level the measurement — the geometric
        # counterpart of what deskew() does to the pixels.
        angle = 3.0
        words = make_lines(8, 6, angle)
        theta = math.radians(angle)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        levelled = []
        for w in words:
            cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
            lx = cos_t * cx + sin_t * cy
            ly = -sin_t * cx + cos_t * cy
            hw, hh = (w.x1 - w.x0) / 2, (w.y1 - w.y0) / 2
            levelled.append(
                Word(
                    round(lx - hw), round(ly - hh),
                    round(lx + hw), round(ly + hh),
                    w.text, w.line_key,
                )
            )
        assert measure_skew_deg(levelled) == pytest.approx(0.0, abs=0.15)


# --------------------------------------------------------------------------
# Block emission from the cut tree
# --------------------------------------------------------------------------


def term() -> Node:
    return Node(words=[])


def cut(axis: str, *children: Node) -> Node:
    return Node(words=[], axis=axis, children=list(children))


class TestBlockEmission:
    def test_h_cuts_are_block_boundaries(self):
        # title / body stacked by an h cut -> two blocks.
        tree = cut("h", term(), term())
        text = assemble_block_text(tree, ["TITLE\n", "BODY LINE\n"])
        assert text == "TITLE\n\nBODY LINE"

    def test_columns_stay_in_one_block(self):
        # h cut above, v cut below: the v node's two columns are ONE block,
        # left column first, joined by a single newline.
        tree = cut("h", term(), cut("v", term(), term()))
        text = assemble_block_text(
            tree, ["HEAD\n", "left col\n", "right col\n"]
        )
        assert text == "HEAD\n\nleft col\nright col"

    def test_root_master_column_split_separates_blocks(self):
        # Measured tree shape of every real page: the ROOT cut is vertical
        # (sidebar column vs main content). It separates blocks; the h cuts
        # inside each side then delimit that side's blocks.
        tree = cut(
            "v",
            term(),  # sidebar (headnote)
            cut("h", cut("v", term(), term()), term()),
        )
        text = assemble_block_text(
            tree,
            ["headnote\n", "ing left\n", "ing right\n", "steps\n"],
        )
        assert text == "headnote\n\ning left\ning right\n\nsteps"

    def test_deeper_v_is_not_descended(self):
        # A v cut below the root v (e.g. a sub-recipe block: ingredient
        # column beside instruction column) must NOT split into blocks —
        # this is precisely the goddess-sauce case.
        sub_recipe = cut("v", cut("h", term(), term()), term())
        tree = cut("v", term(), cut("h", term(), sub_recipe))
        text = assemble_block_text(
            tree,
            [
                "headnote\n",
                "steps\n",
                "goddess sauce\n",
                "1 avocado\n1 jalapeno\n",
                "In a blender, combine\n",
            ],
        )
        assert text.split("\n\n") == [
            "headnote",
            "steps",
            "goddess sauce\n1 avocado\n1 jalapeno\nIn a blender, combine",
        ]

    def test_internal_blank_lines_collapse_within_block(self):
        # Tesseract sprinkles blank lines inside a terminal's text; inside
        # a block they must not fabricate resolver block boundaries.
        tree = cut("h", term(), term())
        text = assemble_block_text(tree, ["a\n\nb\n", "c\n"])
        assert text == "a\nb\n\nc"

    def test_terminal_only_tree_is_one_block(self):
        assert assemble_block_text(term(), ["only\n"]) == "only"


# --------------------------------------------------------------------------
# Real-photo tests (slow)
# --------------------------------------------------------------------------


@slow_photo
class TestRealPhoto:
    def test_chicken_measures_below_gate(self):
        # The flat reference photo must not trip the gate — its scorecard
        # is required to stay byte-identical (April finding: deskew hurt).
        words = get_words(Image.open(CHICKEN_PHOTO))
        angle = measure_skew_deg(words)
        assert angle is not None
        assert abs(angle) < SKEW_GATE_DEG

    def test_known_rotation_is_recovered(self):
        # Rotate the chicken photo by a known angle and confirm the
        # measurement tracks it. PIL rotate(+3) tilts text 3 deg uphill
        # left-to-right, i.e. the measured angle drops by ~3.
        img = Image.open(CHICKEN_PHOTO)
        base = measure_skew_deg(get_words(img))
        rotated = deskew(img, -3.0)  # deskew(-3) == rotate(-3): tilt +3 down
        measured = measure_skew_deg(get_words(rotated))
        assert base is not None and measured is not None
        # Word re-detection on the rotated photo shifts which lines
        # qualify, so allow +-0.8 deg around the injected 3.0.
        assert measured - base == pytest.approx(3.0, abs=0.8)
