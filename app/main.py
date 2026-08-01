from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forbids browser caching.

    The app is served locally and its JS/CSS change with the code; browsers
    caching stale scripts caused invisible-feature confusion (the nutrition
    breakdown existed on disk but an old cached recipe-detail.js kept
    rendering). Locally, re-reading a few files per page load costs nothing.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

from app.database import create_tables
from app.routers.pantry import router as pantry_router
from app.routers.recipes import router as recipes_router
from app.routers.storage import router as storage_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
PHOTOS_DIR = Path(__file__).resolve().parent.parent / "data" / "photos"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs on startup: create tables if they don't exist
    create_tables()
    yield
    # Runs on shutdown: nothing to clean up yet


app = FastAPI(title="PufferPantry", version="0.1.0", lifespan=lifespan)

# API routes
app.include_router(pantry_router, prefix="/api")
app.include_router(recipes_router, prefix="/api")
app.include_router(storage_router, prefix="/api")

# Static assets (CSS, JS, images) served under their own paths.
# These are mounted AFTER the API router so they don't shadow API routes.
app.mount("/css", NoCacheStaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", NoCacheStaticFiles(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/icons", StaticFiles(directory=FRONTEND_DIR / "icons"), name="icons")
app.mount("/img", StaticFiles(directory=FRONTEND_DIR / "img"), name="img")
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")


@app.get("/")
async def serve_index():
    """Serve the frontend's index.html at the root URL."""
    return FileResponse(FRONTEND_DIR / "index.html")
