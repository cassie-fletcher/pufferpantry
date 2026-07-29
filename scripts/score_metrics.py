"""OCR scoring metrics.

Two layers:

1. STRING metrics (the original harness): (reference, candidate) -> [0, 1],
   validated against synthetic cases (clean.txt / flawed.txt / manifest.json)
   from inject_errors.py. A metric that fails to rank a known-flawed text
   below clean gets discarded — that rule predates everything else here.

2. SCHEMA metrics (added 2026-07-29, slate discussed with and approved by
   Cassie; originally lettered A/B/B'/C/D in that discussion):
     ingredients    field scores (pair GT<->parsed, then per-field accuracy)
     step_text      per-step word error rate, aligned by printed number
     step_numerics  numeric-token recall inside steps (a temp/time/amount
                    wrong is worse than a prose typo)
     page_coverage  raw-text word recall/precision (engine coverage, pre-parse)
     parsed_scalars title similarity + servings (parsed fields, NOT raw text —
                    a method can ace coverage and still fail here if its
                    parser picks a wrong line)

   Several v0 choices inside the schema metrics are explicitly marked
   "DECISION (v0)" and await Cassie's ratification. Do not silently change
   them; do not treat them as settled.

CLI:
  score_metrics.py CASE_DIR | --all      validate string metrics on cases
  score_metrics.py --method NAME        score a reader against ground truth
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, TypedDict

MetricFn = Callable[[str, str], float]
METRICS: dict[str, MetricFn] = {}


def register_metric(name: str) -> Callable[[MetricFn], MetricFn]:
    """Register a metric under `name`.

    Registered fn signature: (reference, candidate) -> float in [0, 1].
    Higher = better match. 1.0 = perfect, 0.0 = maximally bad.
    """
    def decorator(fn: MetricFn) -> MetricFn:
        if name in METRICS:
            raise ValueError(f"metric already registered: {name}")
        METRICS[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# String metrics (layer 1). Used both directly (metric C) and as the
# validation harness for everything else.
# ---------------------------------------------------------------------------

@register_metric("char_recall")
def char_recall(reference: str, candidate: str) -> float:
    """Multiset character recall: shared chars / chars in reference."""
    if not reference:
        return 1.0 if not candidate else 0.0
    ref = Counter(reference)
    cand = Counter(candidate)
    shared = sum((ref & cand).values())
    return shared / sum(ref.values())


@register_metric("word_recall")
def word_recall(reference: str, candidate: str) -> float:
    """Multiset word-level recall, whitespace-tokenized."""
    ref_tokens = reference.split()
    if not ref_tokens:
        return 1.0 if not candidate.split() else 0.0
    ref = Counter(ref_tokens)
    cand = Counter(candidate.split())
    shared = sum((ref & cand).values())
    return shared / sum(ref.values())


@register_metric("word_precision")
def word_precision(reference: str, candidate: str) -> float:
    """Multiset word-level precision: shared words / words in candidate.

    Complement to word_recall for metric C: recall catches omission (engine
    missed page content), precision catches hallucination (engine emitted
    text that is not on the page).
    """
    cand_tokens = candidate.split()
    if not cand_tokens:
        return 1.0 if not reference.split() else 0.0
    ref = Counter(reference.split())
    cand = Counter(cand_tokens)
    shared = sum((ref & cand).values())
    return shared / sum(cand.values())


# ---------------------------------------------------------------------------
# Schema metrics (layer 2): shared text helpers.
# ---------------------------------------------------------------------------

_UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75,
    "⅓": 1 / 3, "⅔": 2 / 3,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}

# Numbers as they appear in recipes: "2", "0.5", "1/2", "425", plus an
# optional degree suffix ("425°F"), plus bare unicode fraction glyphs.
# DECISION (v0): times like "2 hours and 45 minutes" contribute their bare
# numbers ("2", "45"); no attempt to parse durations as durations.
_NUMERIC_TOKEN_RE = re.compile(r"\d+(?:[./]\d+)?(?:°[A-Za-z])?|[¼½¾⅓⅔⅛⅜⅝⅞]")


def to_decimal(text: str | None) -> float | None:
    """Parse a printed quantity to a decimal, or None if non-numeric.

    Handles: "2", "0.5", ".5", "1/2", "1 1/2", "½", "1½", "1 ½".
    This is the canonicalization the ground-truth header prescribes: GT stores
    decimals; everything else is normalized to decimals before comparing.
    """
    if not text:
        return None
    s = text.strip()
    total = 0.0
    seen = False
    for glyph, val in _UNICODE_FRACTIONS.items():
        if glyph in s:
            total += val
            s = s.replace(glyph, " ")
            seen = True
    for part in s.split():
        if re.fullmatch(r"\d+/\d+", part):
            num, den = part.split("/")
            if den == "0":
                return None
            total += int(num) / int(den)
            seen = True
        elif re.fullmatch(r"\d*\.\d+|\d+\.?", part):
            total += float(part)
            seen = True
        else:
            return None  # non-numeric content ("to taste", "1 large") — not a quantity
    return total if seen else None


def _norm_text(s: str) -> str:
    """Lowercase, collapse whitespace, strip edge punctuation per token.

    DECISION (v0): this is the entire canonicalization for word comparison.
    No unit-synonym folding (tbsp != tablespoon), no plural stripping
    (cup != cups — the page's plurality is signal; see the wine-entry fix).
    """
    tokens = (re.sub(r"^\W+|\W+$", "", t) for t in s.lower().split())
    return " ".join(t for t in tokens if t)


def _sim(a: str, b: str) -> float:
    """Similarity in [0,1] between two short normalized strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def numeric_tokens(text: str) -> Counter[str]:
    """Multiset of numeric tokens in `text` (see _NUMERIC_TOKEN_RE)."""
    return Counter(_NUMERIC_TOKEN_RE.findall(text))


def split_numbered_steps(instructions: str | None) -> dict[int, str]:
    """Split an instructions blob into {printed_number: step_text}.

    Accepts both "1. text" and "1.) text" markers at line starts. Printed
    numbers are preserved as-is — a method that drops a step number should
    show a hole here, not a silently renumbered list.
    """
    if not instructions:
        return {}
    parts = re.split(r"(?m)^\s*(\d+)[.)]+\s*", instructions)
    # parts = [preamble, num, text, num, text, ...]
    steps: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        steps[int(parts[i])] = parts[i + 1].strip()
    return steps


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: word-level edit distance / reference length.

    0.0 = perfect. Can exceed 1.0 when the hypothesis is much longer than
    the reference. Plain O(n*m) DP — recipe steps are short.
    """
    ref = _norm_text(reference).split()
    hyp = _norm_text(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else float(len(hyp))
    prev = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        curr = [i] + [0] * len(hyp)
        for j, hw in enumerate(hyp, 1):
            cost = 0 if rw == hw else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1] / len(ref)


# ---------------------------------------------------------------------------
# Metric A: ingredient field scores.
# ---------------------------------------------------------------------------

# DECISION (v0): a GT ingredient and a parsed ingredient are paired greedily
# by name similarity, best pairs first, one-to-one, with this floor. The
# similarity is _sim() on normalized strings, boosted to at least 0.9 when
# every token of the GT item appears in the parsed name — so GT item "garlic"
# pairs with parsed name "garlic cloves, finely chopped or grated, ..." even
# though the raw ratio is low. The boost encodes one side of the unresolved
# GT<->schema mapping question (whether descriptors belong in the name);
# scoring item_sim separately keeps the other side visible.
PAIR_THRESHOLD = 0.55


class IngredientScore(TypedDict):
    recall: float          # GT ingredients that found a partner / all GT
    precision: float       # parsed ingredients that found a partner / all parsed
    quantity_acc: float    # matched pairs with equal decimal quantity
    unit_acc: float        # matched pairs with equal normalized unit
    item_sim: float        # mean name similarity over matched pairs
    n_gt: int
    n_parsed: int
    n_matched: int


def _ing_name_sim(gt_item: str, parsed_name: str) -> float:
    a, b = _norm_text(gt_item), _norm_text(parsed_name)
    score = _sim(a, b)
    gt_tokens = set(a.split())
    if gt_tokens and gt_tokens <= set(b.split()):
        score = max(score, 0.9)
    return score


def match_ingredients(
    gt_ings: list[dict], parsed_ings: list[dict]
) -> list[tuple[int, int, float]]:
    """Greedy one-to-one pairing: (gt_index, parsed_index, similarity)."""
    candidates = [
        (i, j, _ing_name_sim(g.get("item") or "", p.get("name") or ""))
        for i, g in enumerate(gt_ings)
        for j, p in enumerate(parsed_ings)
    ]
    candidates.sort(key=lambda t: (-t[2], t[0], t[1]))  # deterministic
    used_gt: set[int] = set()
    used_parsed: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for i, j, s in candidates:
        if s < PAIR_THRESHOLD or i in used_gt or j in used_parsed:
            continue
        pairs.append((i, j, s))
        used_gt.add(i)
        used_parsed.add(j)
    return pairs


def score_ingredients(gt_ings: list[dict], parsed_ings: list[dict]) -> IngredientScore:
    pairs = match_ingredients(gt_ings, parsed_ings)
    q_ok = u_ok = q_n = u_n = 0
    sims: list[float] = []
    for i, j, s in pairs:
        gt, p = gt_ings[i], parsed_ings[j]
        sims.append(s)
        gt_q = to_decimal(gt.get("quantity"))
        if gt_q is not None:  # GT non-numeric ("to taste") pairs are skipped
            q_n += 1
            p_q = to_decimal(p.get("amount"))
            if p_q is not None and abs(p_q - gt_q) < 1e-9:
                q_ok += 1
        gt_u = _norm_text(gt.get("unit") or "")
        if gt_u:
            u_n += 1
            if _norm_text(p.get("unit") or "") == gt_u:
                u_ok += 1
    return IngredientScore(
        recall=len(pairs) / len(gt_ings) if gt_ings else 1.0,
        precision=len(pairs) / len(parsed_ings) if parsed_ings else 1.0,
        quantity_acc=q_ok / q_n if q_n else 1.0,
        unit_acc=u_ok / u_n if u_n else 1.0,
        item_sim=sum(sims) / len(sims) if sims else 0.0,
        n_gt=len(gt_ings),
        n_parsed=len(parsed_ings),
        n_matched=len(pairs),
    )


# ---------------------------------------------------------------------------
# Metrics B and B': step fidelity.
# ---------------------------------------------------------------------------

class StepScore(TypedDict):
    mean_wer: float          # over steps aligned by printed number (B)
    numeric_recall: float    # GT numeric tokens recovered, aligned steps (B')
    n_gt_steps: int
    n_parsed_steps: int
    n_aligned: int
    missing_numbers: list[int]  # GT step numbers absent from the parse


def score_steps(gt_steps: list[dict], instructions: str | None) -> StepScore:
    parsed = split_numbered_steps(instructions)
    wers: list[float] = []
    num_found = num_total = 0
    missing: list[int] = []
    for step in gt_steps:
        n, gt_text = int(step["number"]), step["text"]
        if n not in parsed:
            missing.append(n)
            continue
        wers.append(wer(gt_text, parsed[n]))
        gt_nums = numeric_tokens(gt_text)
        num_total += sum(gt_nums.values())
        num_found += sum((gt_nums & numeric_tokens(parsed[n])).values())
    return StepScore(
        mean_wer=sum(wers) / len(wers) if wers else 1.0,
        numeric_recall=num_found / num_total if num_total else 1.0,
        n_gt_steps=len(gt_steps),
        n_parsed_steps=len(parsed),
        n_aligned=len(wers),
        missing_numbers=missing,
    )


# ---------------------------------------------------------------------------
# Metric C: raw-text coverage.  Metric D: parsed scalars.
# ---------------------------------------------------------------------------

class CoverageScore(TypedDict):
    word_recall: float
    word_precision: float


def score_raw_coverage(gt_flat_text: str, raw_text: str | None) -> CoverageScore:
    ref, cand = _norm_text(gt_flat_text), _norm_text(raw_text or "")
    return CoverageScore(
        word_recall=word_recall(ref, cand),
        word_precision=word_precision(ref, cand),
    )


class ScalarScore(TypedDict):
    title_sim: float
    servings_ok: bool | None   # None when GT has no parseable serves range


def score_scalars(gt: dict, title: str, servings: int | None) -> ScalarScore:
    gt_title = _norm_text(gt.get("recipe_title") or "")
    # DECISION (v0): GT serves is freeform ("4-6"); parsed servings counts as
    # correct if it falls within the GT range (single value = exact match).
    serves_ints = [int(x) for x in re.findall(r"\d+", gt.get("cook_info", {}).get("serves", ""))]
    if not serves_ints:
        ok = None
    elif servings is None:
        ok = False
    else:
        ok = min(serves_ints) <= servings <= max(serves_ints)
    return ScalarScore(
        title_sim=_sim(gt_title, _norm_text(title or "")),
        servings_ok=ok,
    )


# ---------------------------------------------------------------------------
# Full scorecard for one reading.
# ---------------------------------------------------------------------------

def score_reading(
    gt: dict,
    *,
    ingredients: list[dict],
    instructions: str | None,
    title: str,
    servings: int | None,
    raw_text: str | None,
    gt_flat_text: str,
) -> dict:
    """Score one parsed reading against ground truth. Pure data in, dict out —
    no dependency on the app package or any OCR engine."""
    return {
        "ingredients": score_ingredients(gt["ingredients"], ingredients),
        "steps": score_steps(gt["steps"], instructions),
        "coverage": score_raw_coverage(gt_flat_text, raw_text),
        "scalars": score_scalars(gt, title, servings),
    }


# ---------------------------------------------------------------------------
# Ground-truth checking (--check-gt): structural lint + ingredient-name
# cross-check via parse-ingredients.
#
# parse-ingredients is used for NAMES ONLY (Cassie, 2026-07-29): measured on
# our own lines, it misparses bare fractions ("1/2 cup" -> qty 1.5) and
# raises IndexError on quantity-less lines. Its quantity/unit output must
# never be consumed. Name extraction is decent and is all we take.
# ---------------------------------------------------------------------------

_INGREDIENT_FIELDS = ("quantity", "unit", "item", "prep", "notes")


def lint_ground_truth(gt: dict) -> list[str]:
    """Structural problems in a GT dict. Empty list = clean."""
    problems: list[str] = []
    for key in ("recipe_title", "ingredients", "steps"):
        if not gt.get(key):
            problems.append(f"missing or empty top-level key: {key}")
    for n, ing in enumerate(gt.get("ingredients") or []):
        missing = [f for f in _INGREDIENT_FIELDS if f not in ing]
        if missing:
            problems.append(f"ingredient[{n}]: missing fields {missing}")
        if not (ing.get("item") or "").strip():
            problems.append(f"ingredient[{n}]: empty item")
        q = ing.get("quantity", "")
        if q and to_decimal(q) is None:
            problems.append(
                f"ingredient[{n}] ({ing.get('item')!r}): quantity {q!r} is neither "
                "empty nor decimal-parseable"
            )
    numbers = [int(s["number"]) for s in gt.get("steps") or [] if "number" in s]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        problems.append(f"step numbers {numbers} != consecutive {expected}")
    items = [_norm_text(i.get("item") or "") for i in gt.get("ingredients") or []]
    for dup in {i for i in items if items.count(i) > 1}:
        problems.append(f"duplicate ingredient item: {dup!r}")
    return problems


def check_ingredient_names(gt: dict) -> list[str]:
    """Cross-check each GT item against parse-ingredients' name extraction.

    Reconstructs an approximation of the printed line from the GT fields,
    parses it, and flags entries where the library's extracted name and the
    GT item don't overlap. Flags are CANDIDATES for a human to judge against
    the photo — the library is not an authority on what the page says.
    """
    import contextlib
    import io

    from parse_ingredients import parse_ingredient

    flags: list[str] = []
    for n, ing in enumerate(gt.get("ingredients") or []):
        line = " ".join(
            p for p in (ing.get("quantity"), ing.get("unit"), ing.get("item")) if p
        )
        if ing.get("prep"):
            line += f", {ing['prep']}"
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # silence its debug prints
                parsed = parse_ingredient(line)
        except Exception as exc:  # crashes on quantity-less lines, measured
            flags.append(f"ingredient[{n}] ({ing.get('item')!r}): unparseable ({type(exc).__name__}) — skipped")
            continue
        gt_item, lib_name = _norm_text(ing.get("item") or ""), _norm_text(parsed.name)
        if not lib_name:
            flags.append(f"ingredient[{n}] ({ing.get('item')!r}): library extracted no name")
        elif lib_name != gt_item and lib_name not in gt_item and gt_item not in lib_name:
            flags.append(
                f"ingredient[{n}]: GT item {ing.get('item')!r} vs library name {parsed.name!r}"
            )
    return flags


# ---------------------------------------------------------------------------
# Scorer.
# ---------------------------------------------------------------------------

class MetricResult(TypedDict):
    clean: float
    flawed: float
    delta: float
    flawed_ranked_below_clean: bool


def _summarize_manifest(manifest: list[dict]) -> dict:
    """Compact per-case summary of injected error events."""
    type_counts: Counter[str] = Counter(e.get("type", "unknown") for e in manifest)
    chars_deleted = sum(
        int(e.get("length", 0)) for e in manifest if e.get("type") == "missing_content"
    )
    chars_substituted = sum(
        1 for e in manifest if e.get("type") in ("char_misread", "glyph_confusion")
    )
    interleave_events = int(type_counts.get("interleave", 0))
    return {
        "total_events": len(manifest),
        "type_counts": dict(type_counts),
        "chars_deleted": chars_deleted,
        "chars_substituted": chars_substituted,
        "interleave_events": interleave_events,
    }


def _load_case(case_dir: Path) -> tuple[str, str, list[dict]]:
    clean = (case_dir / "clean.txt").read_text(encoding="utf-8")
    flawed = (case_dir / "flawed.txt").read_text(encoding="utf-8")
    manifest_raw = (case_dir / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, list):
        raise ValueError(f"{case_dir}/manifest.json must be a list of dicts")
    return clean, flawed, manifest


def score_case(case_dir: Path) -> dict:
    """Score one synthetic case directory against every registered metric."""
    clean, flawed, manifest = _load_case(case_dir)
    metrics: dict[str, MetricResult] = {}
    for name, fn in METRICS.items():
        clean_score = fn(clean, clean)
        flawed_score = fn(clean, flawed)
        metrics[name] = {
            "clean": clean_score,
            "flawed": flawed_score,
            "delta": flawed_score - clean_score,
            "flawed_ranked_below_clean": flawed_score < clean_score,
        }
    return {
        "case_name": case_dir.name,
        "manifest_summary": _summarize_manifest(manifest),
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _format_summary_line(summary: dict) -> str:
    parts = [f"{n} {t}" for t, n in sorted(summary["type_counts"].items())]
    extras: list[str] = []
    if summary["chars_deleted"]:
        extras.append(f"{summary['chars_deleted']} chars deleted")
    if summary["chars_substituted"]:
        extras.append(f"{summary['chars_substituted']} chars substituted")
    body = ", ".join(parts) if parts else "no events"
    if extras:
        body += "; " + ", ".join(extras)
    return body


def _print_report(result: dict) -> None:
    header = f"Case: {result['case_name']}  ({_format_summary_line(result['manifest_summary'])})"
    print(header)
    print()
    print(f"{'Metric':<14} {'Clean':<8} {'Flawed':<8} {'Delta':<8} Flawed < Clean?")
    for name, m in result["metrics"].items():
        flag = "YES" if m["flawed_ranked_below_clean"] else "NO"
        print(
            f"{name:<14} {m['clean']:<8.3f} {m['flawed']:<8.3f} "
            f"{m['delta']:<+8.3f} {flag}"
        )
    print()


def _iter_case_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir())


def _print_scorecard(method: str, report: dict) -> None:
    a, b, c, d = (report["ingredients"], report["steps"],
                  report["coverage"], report["scalars"])
    print(f"=== {method} ===")
    print(f"ingredient recovery   recall {a['recall']:.2f}  precision {a['precision']:.2f}  "
          f"({a['n_matched']}/{a['n_gt']} GT matched, {a['n_parsed']} parsed)")
    print(f"ingredient fields     quantity {a['quantity_acc']:.2f}  unit {a['unit_acc']:.2f}  "
          f"item_sim {a['item_sim']:.2f}")
    print(f"step text             mean WER {b['mean_wer']:.3f}  "
          f"({b['n_aligned']}/{b['n_gt_steps']} aligned"
          + (f", missing {b['missing_numbers']}" if b["missing_numbers"] else "") + ")")
    print(f"step numerics         recall {b['numeric_recall']:.2f}   "
          "(amounts/temps/times inside steps)")
    print(f"page coverage         word recall {c['word_recall']:.2f}  "
          f"precision {c['word_precision']:.2f}   (raw text, pre-parse)")
    servings = {True: "ok", False: "WRONG", None: "n/a"}[d["servings_ok"]]
    print(f"title & servings      title_sim {d['title_sim']:.2f}  servings {servings}")
    print()


def _score_method(method: str) -> int:
    """Read the reference photo with `method` and print its scorecard."""
    import yaml

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from app.ocr import read_image  # deferred: needs repo root on sys.path
    from inject_errors import render_ground_truth

    gt_path = Path(__file__).parent / "ground_truth" / "20260404_204704_04f01b.yaml"
    gt = yaml.safe_load(gt_path.read_text(encoding="utf-8"))
    photo = repo / "data" / "photos" / gt["photo"]

    reading = read_image(photo, method=method)
    s = reading.schema
    report = score_reading(
        gt,
        ingredients=[i.model_dump() for i in s.ingredients],
        instructions=s.instructions,
        title=s.title,
        servings=s.servings,
        raw_text=reading.raw_text,
        gt_flat_text=render_ground_truth(str(gt_path), layout="flat").text,
    )
    _print_scorecard(method, report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score synthetic OCR cases.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("case_dir", nargs="?", type=Path, help="single case dir")
    group.add_argument(
        "--all", action="store_true",
        help="score every subdir of scripts/synthetic_cases/",
    )
    group.add_argument(
        "--method",
        help="score a reader (tesseract|apple_vision|claude) against ground truth",
    )
    group.add_argument(
        "--check-gt", nargs="?", const="", metavar="YAML",
        help="lint a ground-truth file (default: the Sunday Chicken GT)",
    )
    args = parser.parse_args(argv)

    if args.method:
        return _score_method(args.method)

    if args.check_gt is not None:
        import yaml

        path = (Path(args.check_gt) if args.check_gt
                else Path(__file__).parent / "ground_truth" / "20260404_204704_04f01b.yaml")
        gt = yaml.safe_load(path.read_text(encoding="utf-8"))
        problems = lint_ground_truth(gt)
        flags = check_ingredient_names(gt)
        print(f"structural problems ({len(problems)}):")
        for p in problems:
            print(f"  {p}")
        print(f"name-check flags ({len(flags)}):")
        for f in flags:
            print(f"  {f}")
        return 1 if problems else 0

    if args.all:
        root = Path(__file__).parent / "synthetic_cases"
        if not root.is_dir():
            print(f"no synthetic_cases dir at {root}", file=sys.stderr)
            return 1
        for case_dir in _iter_case_dirs(root):
            _print_report(score_case(case_dir))
        return 0

    _print_report(score_case(args.case_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
