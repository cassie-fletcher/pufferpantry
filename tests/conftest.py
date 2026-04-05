"""
Pytest fixtures — reusable setup/teardown for tests.

The key idea: tests use an in-memory SQLite database (sqlite://, no file path)
so they don't touch your real data. Each test gets a fresh database.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models.recipe import Ingredient, Recipe  # noqa: F401 — register models with Base

# In-memory SQLite — exists only while the test runs, then vanishes.
# StaticPool ensures all connections share the same in-memory database
# (by default, each connection to sqlite:// gets its own empty database).
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine)


def override_get_db():
    """Replacement for get_db that uses the test database."""
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


# Tell FastAPI to use our test database instead of the real one
fastapi_app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, drop them after.

    autouse=True means this runs automatically for every test — you don't
    need to explicitly request it.
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    """A test HTTP client that talks to the FastAPI app without starting a real server."""
    return TestClient(fastapi_app)


@pytest.fixture
def sample_recipe():
    """A recipe dict you can POST in tests."""
    return {
        "title": "Sheet Pan Lemon Herb Chicken",
        "meal_type": "dinner",
        "servings": 2,
        "calories_per_serving": 480,
        "instructions": "Preheat oven to 425F. Toss and roast 25 min.",
        "ingredients": [
            {"name": "chicken thighs", "amount": "1", "unit": "lb", "order": 0},
            {"name": "broccoli", "amount": "2", "unit": "cups", "order": 1},
            {"name": "olive oil", "amount": "2", "unit": "tbsp", "order": 2},
        ],
    }
