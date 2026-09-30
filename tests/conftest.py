"""Select a disposable database before importing the API and isolate test history."""

import os
import tempfile

# Always isolate tests, even when the developer configured a production database.
os.environ["PYCONFER_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="pyconfer_tests_"), "history.db"
)

import pytest  # noqa: E402 (configure the test database before importing)

from core import storage  # noqa: E402


@pytest.fixture(autouse=True)
def clean_history():
    """Clear temporary history between tests without deleting an open SQLite file."""
    storage.create_schema()
    with storage._connection() as connection:
        connection.execute("DELETE FROM page")
        connection.execute("DELETE FROM audit")
    yield
