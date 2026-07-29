"""OCR error-injection tool.

Renders a ground-truth YAML recipe into plain text (two layouts) and injects
four classes of OCR-like errors, each with a per-type manifest that makes the
corruption fully reversible.

The renderer returns both the text AND a section line-index map, so injectors
that need structural context (layout damage) use the map directly instead of
re-parsing the text.

Pure + seeded. No file I/O beyond reading the input YAML.
"""
from __future__ import annotations

import argparse
import json
import random
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

LEFT_COL_WIDTH = 42
HEADNOTE_WRAP = 45


@dataclass
class RenderedText:
    """Rendered GT output: the string, plus which line indices belong to
    which semantic section. Injectors that care about structure (layout
    damage) consume this instead of parsing the string.
    """
    text: str
    sections: dict[str, list[int]] = field(default_factory=dict)


def _decimal_to_fraction_glyph(qty: str) -> str:
    """Convert a decimal quantity string to its Unicode fraction-glyph form.

    Matches what cookbook pages typically print: "0.5" -> "½", "1.5" -> "1½",
    "0.25" -> "¼", "0.75" -> "¾". Decimals not representable as a quarter
    fraction (e.g. "0.33") pass through unchanged. Integers and non-numeric
    strings pass through unchanged.
    """
    fractions = {0.25: "¼", 0.5: "½", 0.75: "¾"}
    try:
        n = float(qty)
    except (TypeError, ValueError):
        return qty
    whole = int(n)
    frac = round(n - whole, 4)
    if frac == 0:
        return qty
    if frac in fractions:
        glyph = fractions[frac]
        return f"{whole}{glyph}" if whole > 0 else glyph
    return qty


def _format_ingredient(ing: dict) -> str:
    """One ingredient -> one line. Empty fields dropped."""
    qty = _decimal_to_fraction_glyph(str(ing.get("quantity", "")).strip())
    unit = str(ing.get("unit", "")).strip()
    item = str(ing.get("item", "")).strip()
    prep = str(ing.get("prep", "")).strip()
    notes = str(ing.get("notes", "")).strip()
    head = " ".join(p for p in (qty, unit, item) if p)
    if prep:
        head = f"{head}, {prep}" if head else prep
    if notes:
        head = f"{head} ({notes})" if head else f"({notes})"
    return head.strip()


def _render_cook_info(cook: dict) -> list[str]:
    order = ["prep_time", "cook_time", "total_time", "oven_temp", "serves"]
    labels = {
        "prep_time": "PREP",
        "cook_time": "COOK",
        "total_time": "TOTAL",
        "oven_temp": "OVEN",
        "serves": "SERVES",
    }
    out = []
    for k in order:
        v = cook.get(k, "")
        if v:
            out.append(f"{labels[k]}: {v}")
    return out


def _render_two_column_ingredients(ings: list[dict]) -> list[str]:
    """Render ingredients as two columns, left-padded to LEFT_COL_WIDTH."""
    lines = [_format_ingredient(i) for i in ings]
    if len(lines) < 2:
        return lines
    half = (len(lines) + 1) // 2
    left, right = lines[:half], lines[half:]
    while len(right) < len(left):
        right.append("")
    out = []
    for l, r in zip(left, right):
        out.append(f"{l:<{LEFT_COL_WIDTH}}{r}".rstrip())
    return out


def render_ground_truth(
    yaml_path: str, layout: str = "tesseract-like"
) -> RenderedText:
    """Render a ground-truth YAML into text + section line-index map.

    The section map is built as each block is emitted, so it is authoritative
    by construction — no later parsing needed.
    """
    data = yaml.safe_load(Path(yaml_path).read_text())
    title = data.get("recipe_title", "")
    headnote = (data.get("headnote") or "").strip() or "<HEADNOTE>"
    ings = data.get("ingredients", []) or []
    cook = data.get("cook_info", {}) or {}
    steps = data.get("steps", []) or []
    sidebar = (data.get("notes_sidebar") or "").strip()

    blocks: list[str] = []
    sections: dict[str, list[int]] = {
        "title": [], "headnote": [], "cook_info": [],
        "ingredients": [], "steps": [], "notes_sidebar": [],
    }

    def emit(section: str, lines: list[str]) -> None:
        for ln in lines:
            sections[section].append(len(blocks))
            blocks.append(ln)

    def emit_blank() -> None:
        blocks.append("")

    if title:
        emit("title", [title])
        emit_blank()

    if layout == "tesseract-like":
        wrapped = textwrap.fill(headnote, width=HEADNOTE_WRAP)
        emit("headnote", wrapped.split("\n"))
        emit_blank()
        emit("cook_info", _render_cook_info(cook))
        emit_blank()
        emit("ingredients", _render_two_column_ingredients(ings))
        emit_blank()
    elif layout == "flat":
        emit("headnote", [headnote])
        emit_blank()
        emit("cook_info", _render_cook_info(cook))
        emit_blank()
        emit("ingredients", [_format_ingredient(i) for i in ings])
        emit_blank()
    else:
        raise ValueError(f"unknown layout: {layout}")

    emit("steps", [f"{s['number']}. {s['text'].strip()}" for s in steps])

    if sidebar:
        emit_blank()
        emit("notes_sidebar", [f"NOTE: {sidebar}"])

    return RenderedText(text="\n".join(blocks), sections=sections)


# ---------------------------------------------------------------------------
# Injector 1: character misreads (small confusion table)
# ---------------------------------------------------------------------------

CONFUSION_TABLE: list[tuple[str, str]] = [
    ("F", "R"), ("R", "F"),
    ("°", "*"),
    ("o", "0"), ("0", "o"),
    ("1", "l"), ("l", "1"),
    ("rn", "m"),
    ("m", "rn"),
    ("pot", "put"),
    ("B", "8"), ("8", "B"),
    ("S", "5"), ("5", "S"),
]


def _find_confusion_sites(text: str) -> list[tuple[int, str, str]]:
    """All (offset, original, replacement) sites from the confusion table."""
    sites = []
    for original, replacement in CONFUSION_TABLE:
        start = 0
        while True:
            idx = text.find(original, start)
            if idx < 0:
                break
            sites.append((idx, original, replacement))
            start = idx + 1
    return sites


def _pick_nonoverlapping(
    sites: list[tuple[int, str, str]], n: int, rng: random.Random
) -> list[tuple[int, str, str]]:
    """Seeded, non-overlapping selection of n sites."""
    rng.shuffle(sites)
    picked: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for off, orig, repl in sites:
        end = off + len(orig)
        if any(not (end <= a or off >= b) for a, b in taken):
            continue
        picked.append((off, orig, repl))
        taken.append((off, end))
        if len(picked) == n:
            break
    if len(picked) < n:
        raise ValueError(f"only {len(picked)} non-overlapping sites; need {n}")
    return picked


def inject_char_misreads(
    text: str, n_errors: int, seed: int
) -> tuple[str, list[dict]]:
    """Apply n_errors confusion-table substitutions at seeded sites."""
    rng = random.Random(seed)
    sites = _find_confusion_sites(text)
    picked = _pick_nonoverlapping(sites, n_errors, rng)
    manifest = [
        {"type": "char_misread", "offset": off, "original": o, "replacement": r}
        for off, o, r in picked
    ]
    out = text
    for entry in sorted(manifest, key=lambda m: -m["offset"]):
        off = entry["offset"]
        out = out[:off] + entry["replacement"] + out[off + len(entry["original"]):]
    return out, manifest


# ---------------------------------------------------------------------------
# Injector 2: glyph confusions (Unicode fractions)
# ---------------------------------------------------------------------------

GLYPH_TABLE: dict[str, list[str]] = {
    "½": ["%", "TA", "1%", "A"],
    "¼": ["1/a"],
    "¾": ["3/s"],
}


def _find_glyph_sites(
    text: str, rng: random.Random
) -> list[tuple[int, str, str]]:
    """All glyph sites with a seeded replacement choice per site."""
    sites = []
    for glyph, options in GLYPH_TABLE.items():
        start = 0
        while True:
            idx = text.find(glyph, start)
            if idx < 0:
                break
            repl = rng.choice(options)
            sites.append((idx, glyph, repl))
            start = idx + len(glyph)
    return sites


def inject_glyph_confusions(
    text: str, n_errors: int, seed: int
) -> tuple[str, list[dict]]:
    """Replace Unicode fractions with visually adjacent OCR corruptions."""
    rng = random.Random(seed)
    sites = _find_glyph_sites(text, rng)
    picked = _pick_nonoverlapping(sites, n_errors, rng)
    manifest = [
        {"type": "glyph_confusion", "offset": off, "original": o, "replacement": r}
        for off, o, r in picked
    ]
    out = text
    for entry in sorted(manifest, key=lambda m: -m["offset"]):
        off = entry["offset"]
        out = out[:off] + entry["replacement"] + out[off + len(entry["original"]):]
    return out, manifest


# ---------------------------------------------------------------------------
# Injector 3: layout damage (cross-section interleave)
# ---------------------------------------------------------------------------

def _line_start_offset(text: str, line_no: int) -> int:
    """Char offset of the start of line `line_no` (0-indexed)."""
    if line_no == 0:
        return 0
    nl = 0
    for i, ch in enumerate(text):
        if ch == "\n":
            nl += 1
            if nl == line_no:
                return i + 1
    return len(text)


def inject_layout_damage(
    rendered: RenderedText, n_interleaves: int, seed: int
) -> tuple[str, list[dict]]:
    """Interleave lines across ingredient/step section boundaries.

    Uses the section map from the renderer directly — no text parsing.
    """
    rng = random.Random(seed)
    text = rendered.text
    ing_idx = rendered.sections.get("ingredients", [])
    step_idx = rendered.sections.get("steps", [])
    if not ing_idx or not step_idx:
        raise ValueError("rendered text has no ingredients/steps sections")

    lines = text.split("\n")
    ing_lines = [(i, lines[i]) for i in ing_idx if lines[i].strip()]
    step_lines = [(i, lines[i]) for i in step_idx]

    plans: list[dict] = []
    for _ in range(n_interleaves):
        direction = rng.choice(["ing_into_steps", "step_into_ings"])
        if direction == "ing_into_steps":
            src_i, src_line = rng.choice(ing_lines)
            dst_i, _ = rng.choice(step_lines)
            plans.append({
                "source_section": "ingredients",
                "source_line_index": src_i,
                "inserted_into_section": "steps",
                "dest_line_index": dst_i,
                "content": src_line + "\n",
            })
        else:
            src_i, src_line = rng.choice(step_lines)
            dst_i = rng.choice(ing_idx)
            plans.append({
                "source_section": "steps",
                "source_line_index": src_i,
                "inserted_into_section": "ingredients",
                "dest_line_index": dst_i,
                "content": src_line + "\n",
            })

    resolved: list[dict] = []
    for p in plans:
        off = _line_start_offset(text, p["dest_line_index"])
        resolved.append({
            "type": "interleave",
            "source_section": p["source_section"],
            "source_line_index": p["source_line_index"],
            "inserted_into_section": p["inserted_into_section"],
            "inserted_at_offset": off,
            "content": p["content"],
        })

    out = text
    for entry in sorted(resolved, key=lambda m: -m["inserted_at_offset"]):
        off = entry["inserted_at_offset"]
        out = out[:off] + entry["content"] + out[off:]

    # Manifest offsets must reflect positions in the FLAWED text.
    manifest_sorted = sorted(resolved, key=lambda m: m["inserted_at_offset"])
    shift = 0
    manifest: list[dict] = []
    for entry in manifest_sorted:
        adjusted = dict(entry)
        adjusted["inserted_at_offset"] = entry["inserted_at_offset"] + shift
        manifest.append(adjusted)
        shift += len(entry["content"])
    return out, manifest


# ---------------------------------------------------------------------------
# Injector 4: missing content
# ---------------------------------------------------------------------------

def inject_missing_content(
    text: str, span_length: int, seed: int
) -> tuple[str, list[dict]]:
    """Delete a single contiguous span of length `span_length`."""
    rng = random.Random(seed)
    if span_length <= 0 or span_length > len(text):
        raise ValueError(f"span_length {span_length} invalid for text len {len(text)}")
    start = rng.randint(0, len(text) - span_length)
    deleted = text[start:start + span_length]
    out = text[:start] + text[start + span_length:]
    manifest = [{
        "type": "missing_content",
        "start_offset": start,
        "length": span_length,
        "deleted_content": deleted,
    }]
    return out, manifest


# ---------------------------------------------------------------------------
# Reversibility
# ---------------------------------------------------------------------------

def reverse_manifest(flawed: str, manifest: list[dict]) -> str:
    """Reconstruct the clean text from flawed + manifest.

    Order matters and depends on entry type:
      - Interleaves (pure insertions) must reverse DESCENDING by flawed-text
        offset so each removal doesn't shift the next entry's position.
      - Substitutions (char/glyph) reverse ASCENDING — restoring length at
        earlier offsets moves later content back to its recorded position.
      - Single-span deletions don't care about order.
    """
    out = flawed

    def _sort_key(entry: dict) -> tuple[int, int]:
        t = entry["type"]
        if t == "interleave":
            return (0, -entry["inserted_at_offset"])
        if t == "missing_content":
            return (1, entry["start_offset"])
        return (2, entry["offset"])

    for entry in sorted(manifest, key=_sort_key):
        t = entry["type"]
        if t == "char_misread" or t == "glyph_confusion":
            off = entry["offset"]
            out = out[:off] + entry["original"] + out[off + len(entry["replacement"]):]
        elif t == "interleave":
            off = entry["inserted_at_offset"]
            content = entry["content"]
            out = out[:off] + out[off + len(content):]
        elif t == "missing_content":
            off = entry["start_offset"]
            out = out[:off] + entry["deleted_content"] + out[off:]
        else:
            raise ValueError(f"unknown manifest entry type: {t}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

INJECTORS: dict[str, Callable[..., tuple[str, list[dict]]]] = {
    "char_misreads": inject_char_misreads,
    "glyph_confusions": inject_glyph_confusions,
    "layout_damage": inject_layout_damage,
    "missing_content": inject_missing_content,
}


def _resolve_case(case: str) -> Path:
    """Resolve a case name to a YAML path under scripts/ground_truth/."""
    base = Path(__file__).parent / "ground_truth"
    p = (base / case)
    if p.suffix != ".yaml":
        p = p.with_suffix(".yaml")
    if not p.exists():
        raise FileNotFoundError(f"no ground-truth YAML at {p}")
    return p


def _run_one(
    yaml_path: Path, injector_name: str, params: dict, layout: str
) -> tuple[str, str, list[dict]]:
    rendered = render_ground_truth(str(yaml_path), layout=layout)
    injector = INJECTORS[injector_name]
    if injector_name == "layout_damage":
        flawed, manifest = injector(rendered, **params)
    else:
        flawed, manifest = injector(rendered.text, **params)
    return rendered.text, flawed, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR error injector")
    parser.add_argument("--case", required=True, help="YAML stem under ground_truth/")
    parser.add_argument("--errors", required=True, choices=list(INJECTORS))
    parser.add_argument("--params", default="{}", help="JSON dict of injector kwargs")
    parser.add_argument("--layout", default="tesseract-like")
    args = parser.parse_args()

    params = json.loads(args.params)
    yaml_path = _resolve_case(args.case)
    clean, flawed, manifest = _run_one(yaml_path, args.errors, params, args.layout)
    print("=== clean.txt ===")
    print(clean)
    print("=== flawed.txt ===")
    print(flawed)
    print("=== manifest.json ===")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


def _demo() -> None:
    """Demo one case per injector class. Prints only; writes nothing."""
    yaml_path = _resolve_case("20260404_204704_04f01b")
    plans = [
        ("char_misreads", {"n_errors": 5, "seed": 42}),
        ("glyph_confusions", {"n_errors": 2, "seed": 42}),
        ("layout_damage", {"n_interleaves": 2, "seed": 42}),
        ("missing_content", {"span_length": 60, "seed": 42}),
    ]
    for name, params in plans:
        print(f"\n########## INJECTOR: {name} ##########")
        clean, flawed, manifest = _run_one(yaml_path, name, params, "tesseract-like")
        print(f"--- would-write: cases/{name}/clean.txt ---")
        print(clean)
        print(f"--- would-write: cases/{name}/flawed.txt ---")
        print(flawed)
        print(f"--- would-write: cases/{name}/manifest.json ---")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        main()
    else:
        _demo()
