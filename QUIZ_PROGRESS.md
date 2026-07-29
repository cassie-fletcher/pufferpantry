# Code Quiz Progress

## Date: 2026-04-10

## Completed
- `app/services/photo_service.py` — full trace through extract_recipe_from_photos, save_photo, voting, fraction disagreements, parse_claude_json
- `app/routers/recipes.py` — all endpoints, decorator syntax, prefixes, response_model
- `app/services/recipe_service.py` — create, update (pop + clear pattern), delete, refresh
- `app/services/url_service.py` — HTML fetching, text extraction, strip_tags, prefilling, Sonnet vs Opus
- `app/main.py` — router mounting, prefix stacking (/api + /recipes)
- `app/routers/pantry.py` — scan, bulk, zone-bulk endpoints
- `app/services/pantry_service.py` — CRUD, apply_zone_scan_results, flush vs commit
- `app/services/shopping_service.py` — normalization, consolidation, unit conversion, categorization
- `frontend/index.html` — three-tab layout
- `frontend/js/app.js` — tab switching, filters, dropdown menu, loadRecipes, all wiring
- `frontend/js/components/photo-upload.js` — drag-and-drop, thumbnails, scan flow
- `frontend/js/components/url-import.js` — URL input, prefilling, error handling
- `frontend/js/components/recipe-card.js` — card display, escapeHtml, callbacks
- `frontend/js/components/recipe-detail.js` — full view, ratings (optimistic UI), nutrition (async load), photo repositioning
- `frontend/js/components/recipe-form.js` — create/edit/review modes, ingredient groups, amount_confidence warnings, collectAllIngredients
- `frontend/js/components/shopping-list.js` — category display, check-off (frontend only), remove items
- `frontend/js/components/pantry-list.js` — flat vs zone modes, quantity bar, expiry badges, level picker
- `frontend/js/components/pantry-form.js` — add/edit single pantry item
- `frontend/js/components/pantry-scan.js` — generic vs zone-aware modes, review phase, bulk save

## Not yet covered
- `app/ocr/` — read_image + per-method readers (tesseract, vision, claude), ~1,500 lines, agent-written 2026-07-29, untraced
- `app/config.py` — app configuration/settings
- `app/database.py` — database setup
- `app/services/nutrition_service.py` — USDA nutrition lookup
- `app/models/recipe.py`, `app/models/pantry.py`, `app/models/storage.py` — database table definitions
- `app/schemas/recipe.py`, `app/schemas/pantry.py`, `app/schemas/storage.py` — validation schemas
- `app/routers/storage.py` + `app/services/storage_service.py` — storage area/zone CRUD
- `frontend/js/components/storage-setup.js` — zone setup wizard
- `tests/test_recipes.py` — test suite

## Key concepts Cassie learned
- Decorators (@router.post) — labels that connect functions to URLs
- async/await — waiting for slow operations without freezing the page
- Arrow functions (=>) — shorthand for functions in JS
- Prefilling — putting words in Claude's mouth to skip preamble
- Optimistic UI — update the display before the server confirms
- Transactions — flush vs commit, all-or-nothing saves
- response_model — schema that controls what the API sends back
- Models vs schemas — database structure vs API validation (separate layers)
- db.refresh() — pull database-generated values (like id) back into Python object

## TODOs added during quiz
- Downgrade amount_confidence when voting is triggered
- Pantry depletion tracking (key missing feature) + intended design flow documented in CLAUDE.md
