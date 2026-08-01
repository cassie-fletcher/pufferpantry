"""Auto-tagging for extracted recipes (Cassie's rules, 2026-08-01).

Protein: inferred locally from the TITLE first, then the INGREDIENT names —
her rule: "look in the ingredients and/or title to get protein". Cuisine:
only from a site's own keyword/cuisine metadata (URL imports); a photographed
page has no keyword source, so photo imports leave cuisine blank for her
review — no guessing.

Tags land in the REVIEW form, where she edits before saving — inferred
values are reviewable by construction, never silently persisted.

The vocabularies seed from her existing DB tag values plus common staples.
Matching is word-boundary, longest-first, accent-folded.
"""

from __future__ import annotations

import re
import unicodedata

# Protein vocabulary: DB values (beef, chicken, prosciutto, salmon, turkey,
# vegetarian) + common proteins. Longest-first so "ground turkey" beats
# "turkey" ties and multi-word entries match before their substrings.
PROTEIN_KEYWORDS: tuple[str, ...] = (
    "prosciutto", "chicken", "salmon", "shrimp", "turkey", "scallop",
    "halibut", "tilapia", "sausage", "chorizo", "brisket", "meatball",
    "tofu", "tempeh", "steak", "pork", "lamb", "bacon", "tuna", "cod",
    "crab", "duck", "beef", "egg",
)

# Cuisine vocabulary: her DB values + neighbors. Matched against a site's
# keywords/cuisine strings only.
CUISINE_KEYWORDS: tuple[str, ...] = (
    "mediterranean", "vietnamese", "american", "japanese", "italian",
    "mexican", "chinese", "spanish", "korean", "indian", "french",
    "cuban", "greek", "thai", "asian",
)


def _fold(text: str) -> str:
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    return folded.lower()


def _find_keyword(text: str, vocabulary: tuple[str, ...]) -> str | None:
    folded = _fold(text)
    for word in vocabulary:
        if re.search(rf"\b{re.escape(word)}\b", folded):
            return word
    return None


def infer_protein(title: str | None, ingredient_names: list[str]) -> str | None:
    """Protein tag from the title, else the ingredient list, else None.

    Title wins because it names the dish's identity ("Sesame Salmon Bowls");
    ingredient order breaks ties in the list scan (first match in reading
    order — main proteins are printed before garnishes).
    """
    if title:
        found = _find_keyword(title, PROTEIN_KEYWORDS)
        if found:
            return found
    for name in ingredient_names:
        found = _find_keyword(name, PROTEIN_KEYWORDS)
        if found:
            return found
    return None


def infer_cuisine_from_keywords(keyword_text: str | None) -> str | None:
    """Cuisine tag from a site's keyword/cuisine metadata text, else None.

    Returned capitalized to match the existing tag style ("Asian")."""
    if not keyword_text:
        return None
    found = _find_keyword(keyword_text, CUISINE_KEYWORDS)
    return found.capitalize() if found else None
