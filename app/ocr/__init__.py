"""Read a recipe photo with a chosen method.

    from app.ocr import read_image
    reading = read_image("data/photos/xyz.jpg", method="apple_vision")
    reading.schema.ingredients

Adding a method is one new module exposing `read(image_path, **opts) -> Reading`
plus one entry in `_MODULES` below. Nothing else changes.

Methods are imported lazily, on use. That matters: `claude` pulls in settings
and needs an API key, and `apple_vision` needs a compiled Swift binary. An
eager import would make one method's missing prerequisite break all of them.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Sequence

from app.ocr.base import Reading

__all__ = ["read_image", "Reading", "METHODS"]

# method name -> module that provides read(). Keys are what callers type.
_MODULES: dict[str, str] = {
    "tesseract": "app.ocr.tesseract",
    "apple_vision": "app.ocr.vision",
    "claude": "app.ocr.claude",
}

METHODS: tuple[str, ...] = tuple(_MODULES)


def read_image(
    image_path: str | Path | Sequence[str | Path], *, method: str, **opts: Any
) -> Reading:
    """Read one image — or an ordered list of pages — and return the recipe.

    A list means one recipe spanning several pages: list order is page order,
    and the FIRST entry is the page with the title. Each method handles the
    combination its own way (local engines OCR per page and parse the joined
    result; claude sends all pages in one message). Always one Reading out.

    `method` is required rather than defaulted — picking a winner is a decision
    for the caller to make explicitly, not something this function does quietly.

    Extra keyword arguments are passed through to the method unchanged. They are
    method-specific by nature (Tesseract has `psm`, Vision has
    `recognition_level`); each method validates its own and raises on anything
    it doesn't recognise, so a typo fails loudly instead of being ignored.
    """
    if method not in _MODULES:
        raise ValueError(
            f"unknown method {method!r}. Available: {', '.join(METHODS)}"
        )

    if isinstance(image_path, (list, tuple)):
        pages = [Path(p) for p in image_path]
        if not pages:
            raise ValueError("image_path list is empty")
        for p in pages:
            if not p.is_file():
                raise FileNotFoundError(f"no such image: {p}")
        arg: Path | list[Path] = pages if len(pages) > 1 else pages[0]
    else:
        arg = Path(image_path)
        if not arg.is_file():
            raise FileNotFoundError(f"no such image: {arg}")

    module = importlib.import_module(_MODULES[method])
    reading = module.read(arg, **opts)

    # The dispatch table and the module's own METHOD_NAME must agree, or a
    # voter counting by `reading.method` would mis-attribute results.
    if reading.method != method:
        raise RuntimeError(
            f"{_MODULES[method]}.read() returned method={reading.method!r}, "
            f"expected {method!r}"
        )
    return reading
