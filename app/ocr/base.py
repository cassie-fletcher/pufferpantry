"""The one type every OCR method returns.

Each method reads an image and produces a parsed recipe. They differ wildly in
how they get there — Tesseract and Apple Vision produce text and then parse it,
Claude returns structured JSON directly and never has a loose-text stage — so
`raw_text` is optional and means "what this method read before parsing, if that
concept applies to it".

`method` is the identity a voter would count by. Two configurations of the same
engine are two methods, not one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.recipe import PhotoExtractResult


@dataclass(frozen=True)
class Reading:
    """One method's complete reading of one image."""

    method: str
    schema: PhotoExtractResult
    raw_text: str | None = None
