"""Intermediate representation shared by every OCR engine adapter.

THE COORDINATE CONVENTION, stated once and never deviated from:

    Every BBox is in IMAGE PIXEL space, origin TOP-LEFT, x increasing RIGHT,
    y increasing DOWN, stored as float (x0, y0, x1, y1) with x0 <= x1 and
    y0 <= y1, relative to the image as decoded by PIL at its native size.

Why this one:
  - Pixels, not normalized: you will compare these to what you see in
    matplotlib, and imshow is in pixels. Normalized coords force a mental
    multiply on every debug.
  - Top-left origin: matches PIL, OpenCV, numpy indexing (arr[y, x]),
    matplotlib.imshow, and Tesseract. Apple Vision is the only engine that
    disagrees, so exactly one adapter carries the conversion burden.
  - Corners, not (x, y, w, h): IoU, containment, union, and clipping are the
    operations the ROVER grouping stage actually performs, and all of them are
    one-liners on corners. `to_xywh()` exists for matplotlib's Rectangle.
  - float, not int: Vision returns fractional pixels. Rounding at ingest
    discards sub-pixel information and cannot be undone.

The other load-bearing idea here is the canonical/raw split on Token:
`text` is CANONICAL and is what the voter compares (so "1/2" and "½" land on
the same arc and vote together instead of tying 1-1), while `raw_text` is
exactly what the engine emitted and is never destroyed. Canonicalize the key,
retain every value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

Granularity = Literal["word", "line"]


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned box in canonical image-pixel space (see module docstring)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        # This invariant is cheap and catches the single most likely bug in the
        # whole package: the Apple Vision y-flip applied without swapping which
        # edge becomes y0. See engines/apple_vision.py.
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(
                f"BBox violates x0<=x1, y0<=y1: "
                f"({self.x0}, {self.y0}, {self.x1}, {self.y1})"
            )

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return 0.5 * (self.x0 + self.x1)

    @property
    def cy(self) -> float:
        return 0.5 * (self.y0 + self.y1)

    def to_xywh(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) — the form matplotlib.patches.Rectangle wants."""
        return (self.x0, self.y0, self.width, self.height)

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> BBox:
        return cls(float(x), float(y), float(x) + float(w), float(y) + float(h))

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersect_area(self, other: BBox) -> float:
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        if dx <= 0.0 or dy <= 0.0:
            return 0.0
        return dx * dy

    def iou(self, other: BBox) -> float:
        inter = self.intersect_area(other)
        if inter == 0.0:
            return 0.0
        denom = self.area + other.area - inter
        return inter / denom if denom > 0.0 else 0.0

    def containment(self, other: BBox) -> float:
        """intersect / min(area).

        ~1.0 exactly when one box sits inside the other, which is the
        signature of "engine A merged two lines that engine B split". That is
        the case the cross-engine grouping most needs to get right, and it is
        the case plain IoU handles worst (a half-line inside a merged line
        tops out near 0.5).

        Caveat this buys: a tiny spurious box — a speck detected as "." — is
        fully contained in everything it touches, so callers must filter by
        minimum area before trusting this.
        """
        inter = self.intersect_area(other)
        if inter == 0.0:
            return 0.0
        denom = min(self.area, other.area)
        return inter / denom if denom > 0.0 else 0.0

    def y_overlap_frac(self, other: BBox) -> float:
        """1-D vertical interval overlap / min(height).

        Robust to engines disagreeing about a line's left/right extent, which
        is the *common* disagreement. Used to gate `containment` so that two
        boxes at the same height in different columns don't get grouped.
        """
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        if dy <= 0.0:
            return 0.0
        denom = min(self.height, other.height)
        return dy / denom if denom > 0.0 else 0.0

    def x_overlap_frac(self, other: BBox) -> float:
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        if dx <= 0.0:
            return 0.0
        denom = min(self.width, other.width)
        return dx / denom if denom > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class Token:
    """One voting unit — a word, in the granularity we vote at.

    `text` is canonical and is the vote key. `raw_text` is what the engine
    literally emitted and is carried through to the end so that any
    disagreement can be traced back to the bytes that caused it.
    """

    text: str
    raw_text: str
    box: BBox
    conf: float
    """Normalized to [0, 1]. Filled by the confidence stage, NOT by adapters."""
    raw_conf: float
    """Engine-native scale: 0-100 for Tesseract, 0-1 for Vision."""
    engine: str
    conf_is_inherited: bool = False
    """True when this word's confidence was copied down from its line.

    Apple Vision reports confidence per LINE, not per word. Copying the line
    value onto each word is the only option, but it is fabricated data: a line
    that is 90% right and 10% garbage assigns the garbage the same high
    confidence as the good words. Marking it keeps the fabrication visible in
    the data instead of silently biasing the C(w) term in the vote.
    """

    def __post_init__(self) -> None:
        if not 0.0 <= self.conf <= 1.0:
            raise ValueError(f"conf must be in [0,1], got {self.conf!r}")


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One line as a single engine saw it."""

    tokens: tuple[Token, ...]
    box: BBox
    conf: float
    raw_conf: float
    engine: str
    line_id: str
    """Stable within a run, e.g. "tesseract:b2/p0/l7" or "vision:l0003"."""
    alternates: tuple[tuple[Token, ...], ...] = ()
    """n-best alternative readings of this whole line (Vision topCandidates)."""

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def raw_text(self) -> str:
        return " ".join(t.raw_text for t in self.tokens)


@dataclass(frozen=True, slots=True)
class OcrPage:
    """Everything one engine, at one configuration, read from one image.

    `engine` is the VOTER IDENTITY, not the software name — "tesseract:psm4"
    and "tesseract:psm3" are two voters. ROVER counts voters by this string, so
    getting it wrong silently double-weights an engine.
    """

    engine: str
    engine_version: str
    image_path: str
    image_size: tuple[int, int]
    """(width, height) in pixels, of the ORIGINAL image."""
    lines: tuple[OcrLine, ...]
    warnings: tuple[str, ...] = ()

    @property
    def tokens(self) -> tuple[Token, ...]:
        return tuple(t for line in self.lines for t in line.tokens)

    def text(self, sep: str = "\n") -> str:
        """Flat text in the engine's own emission order.

        This is the naive serialization — it does NOT reconstruct reading order
        across columns. Use the geometry stage for that.
        """
        return sep.join(line.text for line in self.lines)


@runtime_checkable
class OcrEngine(Protocol):
    """Every engine adapter implements exactly this.

    Engine-specific options are validated by each engine's own frozen options
    dataclass inside `run`, so an unknown key raises rather than being silently
    ignored — that failure mode is how you end up believing you tested PSM 6
    when you actually tested PSM 3.
    """

    name: str

    def available(self) -> tuple[bool, str]:
        """(ok, detail). Must not raise; detail says how to fix it if not ok."""
        ...

    def run(self, image_path: Path, **opts: object) -> OcrPage:
        ...
