"""Metric-testing harness for OCR scoring (plumbing only).

Loads a synthetic case (clean.txt, flawed.txt, manifest.json) produced by
inject_errors.py, runs every registered metric against both clean-vs-clean
and clean-vs-flawed, and reports whether each metric correctly ranks the
flawed text below the clean text.

Real metrics are added later via @register_metric. The two metrics defined
here are placeholders that only prove the harness runs end-to-end.
"""
from __future__ import annotations

import argparse
import json
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
# Placeholder metrics (DO NOT expand — real metrics come later with Cassie).
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score synthetic OCR cases.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("case_dir", nargs="?", type=Path, help="single case dir")
    group.add_argument(
        "--all", action="store_true",
        help="score every subdir of scripts/synthetic_cases/",
    )
    args = parser.parse_args(argv)

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
