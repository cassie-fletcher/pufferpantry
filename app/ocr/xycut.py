"""Recursive X-Y cut segmentation for the Tesseract reader.

Why this exists
---------------
Tesseract's own layout analysis (psm 3) zips multi-column regions: on the
reference two-column page it interleaved the ingredient columns and lost
nearly half the entries. Whole-page preprocessing measurably did not help.
What did: detect layout ourselves from Tesseract's word boxes, crop each
block, and OCR blocks individually with layout analysis OFF (psm 6).

This is the template-free version (owner requirement: no assumptions about
where columns are or how many exist). Every threshold derives from the
page's own statistics, none from pixels of a particular photo.

Algorithm
---------
0. Skew gate: measure the page's global tilt from the psm-3 word boxes
   (median per-line baseline angle, lines with >= MIN_SKEW_LINE_WORDS
   words). If |angle| > the gate (SKEW_GATE_DEG), rotate the image level
   (white fill) and redo the word pass on the rotated image; below the
   gate the image is untouched and the pipeline is bit-identical to the
   ungated version. Gated because deskew measurably HURT on the flat
   reference photo (April finding).
1. One psm-3 image_to_data pass -> word boxes.
2. Page statistics: g_med = median same-line inter-word gap (scale for
   vertical cuts), h_med = median word height (scale for horizontal cuts).
3. Recursively split each region on the widest interior word-free band:
   - vertical band qualifies iff width >= k * g_med AND each side's words
     span >= MIN_SIDE_FRAC of the region width. The side-fraction test is
     the quantity/name-seam guard: in a single-column ingredient list the
     seam between the quantity digits and the names is genuinely word-free,
     but the quantity side is a narrow sliver — a true gutter separates two
     WIDE columns.
   - horizontal band qualifies iff height >= k * h_med (no side guard;
     short blocks like titles are legitimate).
4. Terminal regions are cropped tight to their word boxes plus a pad of
   PAD_FRAC * h_med, OCR'd at psm 6, upscaled 3x first when the block's
   text is small. Printed rule lines (extreme-aspect ink not covered by any
   word box) are whitened inside crops; ink the word pass missed entirely
   (display-type titles) is attached to the nearest terminal and, for
   display-height blocks, OCR'd row-by-row at psm 13.
5. If no cut qualifies anywhere, fall through to full-page psm 3 — degrade
   to baseline, never to garbage. (Verified live at k=8.)
6. Reassembly in XY reading order. Two texts come out (XYCutPage):
   raw_text — every terminal joined with blank lines (legacy form, kept
   for scoring/debugging); block_text — the resolver-facing form, where
   blank lines sit exactly at the tree's top-level HORIZONTAL cuts (the
   visual block boundaries) and a block's terminals are joined with
   single newlines in tree reading order (left column first). This stops
   a sub-recipe's ingredient column and its instruction column from
   landing in different resolver blocks.

Validation status — read before trusting
----------------------------------------
Measured on the Sunday Chicken reference page against ground truth
(vs the full-page psm-3 baseline):
    ingredient recall  10/19 -> 13/19   precision 0.91 -> 1.00
    quantity accuracy  0.70  -> 0.92    unit 0.75 -> 0.91
    step WER           0.276 -> 0.23    all 7 steps aligned (was 6, one lost)
    title similarity   0.62  -> 0.88
k has a flat plateau over [1.5, 2.5] on that page; cut-decision probes on
synthetic single-column inputs made no spurious cuts and rejected the
quantity/name seam. BUT: that is one photo. The secondary constants
(MIN_SIDE_FRAC, PAD_FRAC, the ink-pass aspect tests, DISPLAY_HEIGHT_FACTOR,
DEFAULT_UPSCALE_BELOW) were each fixed after observing failures on that
photo — statistic-scaled, not pixel-hardcoded, but only one page has voted
on them. Expect some to shift as more ground truth arrives.

Known ceilings this does NOT fix: the eng traineddata never emits unicode
fraction glyphs (1-1/2 arrives as "1%"), and small-caps meta lines
("SERVES 4 TO 6") stay unreadable.

Skew measurements (2026-07-31, median per-line baseline angle, lines with
>= 4 words):
    chicken reference       -0.53 deg   (flat; gate must not fire — and doesn't)
    salmon page 1 (742219)  +0.69 deg
    salmon page 2 (0a73f4)  +0.55 deg
All three are below the 1-degree gate: the salmon photos' problem is NOT
global tilt. Every page shows a ~3-degree top-to-bottom curvature fan
(top-third line angles ~+1.7, bottom-third ~-1.5) that a global rotation
cannot remove; forcing the rotation anyway (gate 0.3) merely perturbed the
salmon results into a different failure mode, no rescue. Salmon page 1's
real problem is upstream of geometry: psm 3 detects only 236 words there
(vs 560 on page 2), the undetected ingredient panel enters as attached
ink, and the display-title row pass OCRs those full-width rows diagonally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

import cv2
import numpy as np
import pytesseract
from PIL import Image

MIN_SIDE_FRAC = 0.10   # vertical cut: each side >= 10% of region word-span
PAD_FRAC = 1.0         # terminal crop pad = PAD_FRAC * h_med (0.6 lost step
                       # markers on the steps block; 1.0 keeps all seven)
MIN_WORDS_TO_CUT = 4   # don't try to split regions with fewer words

DISPLAY_HEIGHT_FACTOR = 2.0  # block is "display type" if median word height
                             # >= 2 * page h_med; OCR'd row-by-row with psm 13
                             # (psm 6 misreads large decorative type)

DEFAULT_K = 2.0              # cut threshold; plateau [1.5, 2.5] on the
                             # reference page, fall-through observed at 8
DEFAULT_UPSCALE_BELOW = 20.0  # upscale a terminal 3x iff its median word
                              # height (px) is below this

# --- Skew gate constants (all measured 2026-07-31 on the three real pages:
# chicken 20260404_204704_04f01b, salmon 20260405_214432_742219 and
# 20260405_214432_0a73f4) ---

MIN_SKEW_LINE_WORDS = 4   # per-line baseline fit needs >= 4 words: 3-word
                          # lines produced +-6 deg outliers on real pages
MIN_SKEW_LINES = 5        # fewer qualifying lines than this -> measurement
                          # untrusted, no rotation
SKEW_GATE_DEG = 1.0       # rotate only when |median angle| exceeds this.
                          # Justification from the measurements: all three
                          # real pages measure within +-0.7 deg globally,
                          # while each page's INTERNAL line-angle spread
                          # (curvature fan, top vs bottom third) is ~3 deg —
                          # a global rotation below 1 deg is inside the
                          # page's own noise and can only churn pixels. The
                          # flat chicken reference measures -0.53 deg, so
                          # the gate keeps it untouched (April finding:
                          # deskew hurt on that page). A genuinely tilted
                          # page (several degrees, e.g. a photo taken
                          # askew) clears 1 deg with margin.


def _ocr(img: Image.Image, psm: int, lang: str) -> str:
    return pytesseract.image_to_string(img, lang=lang, config=f"--oem 3 --psm {psm}")


def _upscale(img: Image.Image, factor: float) -> Image.Image:
    return img.resize(
        (round(img.width * factor), round(img.height * factor)), Image.LANCZOS
    )


@dataclass
class Word:
    x0: int
    y0: int
    x1: int
    y1: int
    text: str
    line_key: tuple


@dataclass
class Node:
    words: list[Word]
    axis: str | None = None        # "v" | "h" | None (terminal)
    band: tuple[int, int] | None = None
    ratio: float | None = None
    children: list["Node"] = field(default_factory=list)

    def bbox(self) -> tuple[int, int, int, int]:
        return (min(w.x0 for w in self.words), min(w.y0 for w in self.words),
                max(w.x1 for w in self.words), max(w.y1 for w in self.words))


def get_words(img: Image.Image, lang: str = "eng") -> list[Word]:
    d = pytesseract.image_to_data(img, lang=lang, config="--oem 3 --psm 3",
                                  output_type=pytesseract.Output.DICT)
    out = []
    for i in range(len(d["text"])):
        if int(d["conf"][i]) < 0 or not d["text"][i].strip():
            continue
        out.append(Word(d["left"][i], d["top"][i],
                        d["left"][i] + d["width"][i], d["top"][i] + d["height"][i],
                        d["text"][i],
                        (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return out


def line_skew_angles(words: list[Word],
                     min_line_words: int = MIN_SKEW_LINE_WORDS) -> list[float]:
    """Per-line baseline angles, degrees.

    Words are grouped into psm-3 lines (block/par/line ids); each line with
    >= min_line_words words gets a least-squares fit of word-center y over
    word-center x. Positive angle = text slopes downhill left-to-right in
    image coordinates (y down).
    """
    lines: dict[tuple, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line_key, []).append(w)
    angles: list[float] = []
    for ws in lines.values():
        if len(ws) < min_line_words:
            continue
        xs = [(w.x0 + w.x1) / 2 for w in ws]
        ys = [(w.y0 + w.y1) / 2 for w in ws]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            continue  # vertically stacked "line" — no baseline to fit
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        angles.append(math.degrees(math.atan(slope)))
    return angles


def measure_skew_deg(words: list[Word]) -> float | None:
    """Median per-line baseline angle, or None when too few lines qualify.

    The median is the page's global tilt estimate; it is deliberately robust
    to the curvature fan real book photos show (individual lines vary by a
    few degrees top-to-bottom around the global value).
    """
    angles = line_skew_angles(words)
    if len(angles) < MIN_SKEW_LINES:
        return None
    return median(angles)


def deskew(img: Image.Image, angle_deg: float) -> Image.Image:
    """Rotate the image so a page measuring `angle_deg` comes out level.

    PIL rotates counterclockwise; a positive measured angle (text downhill
    to the right) is a clockwise page tilt, so rotating BY the measured
    angle levels it (verified empirically: chicken rotated -3 deg measures
    +2.9, and rotating salmon p1 by its +0.69 brought it to -0.38).
    White fill matches the paper; expand=True keeps corners.
    """
    return img.rotate(angle_deg, resample=Image.BICUBIC, expand=True,
                      fillcolor=(255, 255, 255))


def maybe_deskew(
    img: Image.Image, words: list[Word], gate_deg: float = SKEW_GATE_DEG
) -> tuple[Image.Image, float | None, bool]:
    """The gate: (image, measured angle, fired?).

    Below the gate (or unmeasurable) the ORIGINAL image object is returned
    untouched — bit-identical downstream behavior, per the April finding
    that deskewing a flat page hurt.
    """
    angle = measure_skew_deg(words)
    if angle is None or abs(angle) <= gate_deg:
        return img, angle, False
    return deskew(img, angle), angle, True


def page_stats(words: list[Word]) -> tuple[float, float]:
    """(g_med, h_med): median same-line inter-word gap, median word height."""
    lines: dict[tuple, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line_key, []).append(w)
    gaps = []
    for ws in lines.values():
        ws = sorted(ws, key=lambda w: w.x0)
        for a, b in zip(ws, ws[1:]):
            g = b.x0 - a.x1
            if g > 0:
                gaps.append(g)
    g_med = median(gaps) if gaps else 10.0
    h_med = median(w.y1 - w.y0 for w in words) if words else 20.0
    return g_med, h_med


def _free_bands(intervals: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """Maximal sub-intervals of [lo, hi] covered by no interval."""
    ivs = sorted(intervals)
    bands, cursor = [], lo
    for a, b in ivs:
        if a > cursor:
            bands.append((cursor, a))
        cursor = max(cursor, b)
    # intervals start at lo and end at hi by construction (bbox-tight), so
    # every band found is interior.
    return bands


def best_cut(words: list[Word], k: float, g_med: float, h_med: float):
    """Best qualifying cut for this word set, or None."""
    x0 = min(w.x0 for w in words); x1 = max(w.x1 for w in words)
    y0 = min(w.y0 for w in words); y1 = max(w.y1 for w in words)
    span_w = x1 - x0
    best = None

    for a, b in _free_bands([(w.x0, w.x1) for w in words], x0, x1):
        width = b - a
        ratio = width / g_med
        if ratio < k:
            continue
        left = [w for w in words if w.x1 <= a]
        right = [w for w in words if w.x0 >= b]
        if not left or not right:
            continue
        lw = max(w.x1 for w in left) - min(w.x0 for w in left)
        rw = max(w.x1 for w in right) - min(w.x0 for w in right)
        if lw < MIN_SIDE_FRAC * span_w or rw < MIN_SIDE_FRAC * span_w:
            continue  # quantity/name-seam guard: reject sliver sides
        if best is None or ratio > best[2]:
            best = ("v", (a, b), ratio, left, right)

    for a, b in _free_bands([(w.y0, w.y1) for w in words], y0, y1):
        height = b - a
        ratio = height / h_med
        if ratio < k:
            continue
        top = [w for w in words if w.y1 <= a]
        bottom = [w for w in words if w.y0 >= b]
        if not top or not bottom:
            continue
        if best is None or ratio > best[2]:
            best = ("h", (a, b), ratio, top, bottom)

    return best


def build_tree(words: list[Word], k: float, g_med: float, h_med: float,
               depth: int = 0, max_depth: int = 12) -> Node:
    node = Node(words=words)
    if len(words) < MIN_WORDS_TO_CUT or depth >= max_depth:
        return node
    cut = best_cut(words, k, g_med, h_med)
    if cut is None:
        return node
    axis, band, ratio, first, second = cut
    node.axis, node.band, node.ratio = axis, band, ratio
    node.children = [build_tree(first, k, g_med, h_med, depth + 1, max_depth),
                     build_tree(second, k, g_med, h_med, depth + 1, max_depth)]
    return node


def terminals(node: Node):
    if not node.children:
        yield node
    else:
        for c in node.children:   # children stored in reading order
            yield from terminals(c)


def ink_analysis(img: Image.Image, words: list[Word], h_med: float):
    """Uncovered-ink handling, statistic-scaled and edge-conservative.

    Splits connected components whose pixels are mostly NOT covered by any
    psm-3 word box into:

      rules  — extreme-aspect thin components (aspect >= 10, long dim >=
               1.5*h_med, short dim <= 0.75*h_med). Printed rule lines and
               their JPEG-broken fragments; letter glyphs top out near
               aspect 6. Whitened inside terminal crops. The short-dim cap
               is essential: without it a dark book-edge blob qualifies and
               whitening erases real text. Deliberately NOT a global CC
               filter — that measurably hurt in the April experiments.

      attach — text-sized components inside the page's word extent: ink the
               word detector missed (display-type titles). Attached to the
               nearest terminal so the tight crop cannot slice through it.
    """
    gray = np.array(img.convert("L"))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        (binary > 0).astype(np.uint8), 8)

    covered_mask = np.zeros(gray.shape, bool)
    for w in words:
        covered_mask[w.y0:w.y1, w.x0:w.x1] = True
    covered_px = np.bincount(labels[covered_mask], minlength=n)

    rx0 = min(w.x0 for w in words); rx1 = max(w.x1 for w in words)
    ry0 = min(w.y0 for w in words); ry1 = max(w.y1 for w in words)
    m = h_med  # margin around the page word extent

    rules, attach = [], []
    for i in range(1, n):
        x, y, wd, ht, area = stats[i]
        if area < (0.25 * h_med) ** 2:
            continue
        if covered_px[i] / area > 0.5:
            continue
        long_dim, short_dim = max(ht, wd), min(ht, wd)
        if (long_dim >= 10 * short_dim and long_dim >= 1.5 * h_med
                and short_dim <= 0.75 * h_med):
            rules.append((x, y, x + wd, y + ht))
            continue
        if (x >= rx0 - m and x + wd <= rx1 + m and y >= ry0 - m and y + ht <= ry1 + m
                and ht <= 5 * h_med and area <= (10 * h_med) ** 2):
            attach.append((x, y, x + wd, y + ht))
    return rules, attach


def _rect_dist(a, b) -> float:
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def _y_rows(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Cluster boxes into text rows by y-overlap (>= 50% of smaller height)."""
    rows: list[list[int]] = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for r in rows:
            overlap = min(r[3], b[3]) - max(r[1], b[1])
            if overlap >= 0.5 * min(r[3] - r[1], b[3] - b[1]):
                r[0] = min(r[0], b[0]); r[1] = min(r[1], b[1])
                r[2] = max(r[2], b[2]); r[3] = max(r[3], b[3])
                break
        else:
            rows.append(list(b))
    return [tuple(r) for r in sorted(rows, key=lambda r: r[1])]


def block_nodes(tree: Node, _at_root: bool = True):
    """The visual blocks of the page, in reading order.

    Block boundaries are the tree's top-level HORIZONTAL cuts: stacked
    h-cuts mark title / ingredients / steps / sub-recipe boundaries. One
    wrinkle, measured on all three real pages: the ROOT cut is vertical —
    the full-height gutter between a cookbook's narrow sidebar column and
    its main content. That master column split also separates blocks, so
    the root v-cut (and only the root one) is descended too; each side's
    h-cuts then delimit its blocks. Any DEEPER vertical cut is intra-block
    column structure (a two-column ingredient panel, a sub-recipe's
    ingredient column beside its instruction column) and terminates the
    descent: those columns belong to one block.
    """
    if tree.axis == "h" or (tree.axis == "v" and _at_root):
        for c in tree.children:
            yield from block_nodes(c, _at_root=False)
    else:
        yield tree


def assemble_block_text(tree: Node, terminal_texts: list[str]) -> str:
    """Resolver-facing page text from the cut tree.

    `terminal_texts` is aligned with `terminals(tree)` order. Blank lines
    appear exactly at top-level horizontal cut boundaries; within a block,
    terminal texts are joined with single newlines in tree reading order
    (left column first), internal blank lines dropped.
    """
    texts = {id(t): text for t, text in zip(terminals(tree), terminal_texts)}
    blocks: list[str] = []
    for b in block_nodes(tree):
        lines: list[str] = []
        for t in terminals(b):
            lines.extend(
                ln.rstrip() for ln in texts[id(t)].splitlines() if ln.strip()
            )
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class XYCutPage:
    """One page's xycut output.

    raw_text:   every terminal joined with blank lines — the legacy form,
                kept for raw-text scoring and debugging.
    block_text: resolver-facing form (see assemble_block_text). Identical
                to raw_text's block structure only when every terminal is
                its own visual block.
    skew_deg:   measured global tilt (None = too few lines to measure).
    deskewed:   whether the gate fired and the page was rotated level.
    """
    raw_text: str
    block_text: str
    skew_deg: float | None
    deskewed: bool


def xycut_read(img: Image.Image, *, k: float = DEFAULT_K,
               upscale_below: float = DEFAULT_UPSCALE_BELOW,
               lang: str = "eng", use_ink: bool = True,
               skew_gate_deg: float = SKEW_GATE_DEG) -> XYCutPage:
    """Full pipeline: skew-gate, segment, OCR terminals, reassemble."""
    words = get_words(img, lang=lang)
    if not words:
        text = _ocr(img, psm=3, lang=lang)
        return XYCutPage(text, text, None, False)
    img, skew_deg, deskewed = maybe_deskew(img, words, skew_gate_deg)
    if deskewed:
        words = get_words(img, lang=lang)  # boxes moved; re-detect
        if not words:
            text = _ocr(img, psm=3, lang=lang)
            return XYCutPage(text, text, skew_deg, True)
    g_med, h_med = page_stats(words)
    tree = build_tree(words, k, g_med, h_med)
    if not tree.children:
        # No qualifying cut anywhere: degrade to the full-page psm-3
        # baseline. Its paragraph breaks stand in for block boundaries.
        text = _ocr(img, psm=3, lang=lang)
        return XYCutPage(text, text, skew_deg, deskewed)

    terms = list(terminals(tree))
    boxes = [list(t.bbox()) for t in terms]
    attached: list[list[tuple[int, int, int, int]]] = [[] for _ in terms]
    rules: list[tuple[int, int, int, int]] = []
    if use_ink:
        rules, attach = ink_analysis(img, words, h_med)
        for cc in attach:
            j = min(range(len(boxes)), key=lambda i: _rect_dist(boxes[i], cc))
            attached[j].append(cc)
            boxes[j][0] = min(boxes[j][0], cc[0])
            boxes[j][1] = min(boxes[j][1], cc[1])
            boxes[j][2] = max(boxes[j][2], cc[2])
            boxes[j][3] = max(boxes[j][3], cc[3])

    pad = round(PAD_FRAC * h_med)

    def cropped(x0, y0, x1, y1):
        cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
        cx1, cy1 = min(img.width, x1 + pad), min(img.height, y1 + pad)
        crop = img.crop((cx0, cy0, cx1, cy1))
        for r in rules:  # whiten printed rule lines inside this crop
            ix0, iy0 = max(r[0] - 1, cx0), max(r[1] - 1, cy0)
            ix1, iy1 = min(r[2] + 1, cx1), min(r[3] + 1, cy1)
            if ix0 < ix1 and iy0 < iy1:
                crop.paste((255, 255, 255),
                           (ix0 - cx0, iy0 - cy0, ix1 - cx0, iy1 - cy0))
        return crop

    parts = []
    for t, (x0, y0, x1, y1), ccs in zip(terms, boxes, attached):
        h_blk = median(w.y1 - w.y0 for w in t.words)
        if h_blk >= DISPLAY_HEIGHT_FACTOR * h_med:
            # Display-type block: psm 6 misreads big decorative glyphs.
            # OCR row by row with psm 13, rows clipped at their midlines.
            row_boxes = [(w.x0, w.y0, w.x1, w.y1) for w in t.words] + ccs
            rows = _y_rows(row_boxes)
            edges = [(rows[i][3] + rows[i + 1][1]) // 2 for i in range(len(rows) - 1)]
            lines = []
            for i, r in enumerate(rows):
                top = edges[i - 1] if i > 0 else r[1] - pad
                bot = edges[i] if i < len(edges) else r[3] + pad
                lines.append(_ocr(img.crop((max(0, r[0] - pad), max(0, top),
                                            min(img.width, r[2] + pad),
                                            min(img.height, bot))),
                                  psm=13, lang=lang).strip())
            parts.append("\n".join(lines))
            continue
        crop = cropped(x0, y0, x1, y1)
        if upscale_below and h_blk < upscale_below:
            crop = _upscale(crop, 3)
        parts.append(_ocr(crop, psm=6, lang=lang))
    return XYCutPage(
        raw_text="\n\n".join(parts),
        block_text=assemble_block_text(tree, parts),
        skew_deg=skew_deg,
        deskewed=deskewed,
    )


def xycut_text(img: Image.Image, *, k: float = DEFAULT_K,
               upscale_below: float = DEFAULT_UPSCALE_BELOW,
               lang: str = "eng", use_ink: bool = True,
               skew_gate_deg: float = SKEW_GATE_DEG) -> str:
    """Back-compat wrapper: the raw (per-terminal) text of xycut_read."""
    return xycut_read(img, k=k, upscale_below=upscale_below, lang=lang,
                      use_ink=use_ink, skew_gate_deg=skew_gate_deg).raw_text
