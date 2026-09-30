"""SQLite audit history.

The API persists audit metadata and report rows, but not annotated images.
The OCR engine does not depend on this module."""

import contextlib
import os
import sqlite3

from core.migrations import migrate_legacy_schema, migrate_legacy_values

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_PATH = os.environ.get("PYCONFER_DB", os.path.join(BASE_DIR, "pyconfer.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    job_id           TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    status           TEXT NOT NULL,
    dpi              INTEGER,
    payment_slips_file  TEXT,
    reference_file TEXT,
    total_pages    INTEGER
);

CREATE TABLE IF NOT EXISTS page (
    job_id        TEXT NOT NULL REFERENCES audit(job_id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,
    reference_code  TEXT,
    ocr_code       TEXT,
    code_status TEXT,
    reference_amount  TEXT,
    ocr_amount       TEXT,
    amount_status  TEXT,
    overall_status  TEXT,
    category      TEXT,
    PRIMARY KEY (job_id, page)
);
"""

# Additive migrations for databases created by earlier versions.
ADDED_COLUMNS = (
    ("page", "category", "TEXT"),
)

# Map public report labels to database column names.
_COLUMNS = (
    ("Page", "page"),
    ("Code (Reference PDF)", "reference_code"),
    ("Code (OCR Slips)", "ocr_code"),
    ("Code Status", "code_status"),
    ("Amount (Reference PDF)", "reference_amount"),
    ("Amount (OCR Slips)", "ocr_amount"),
    ("Amount Status", "amount_status"),
    ("Overall Status", "overall_status"),
    ("Category", "category"),
)


def configure(path: str) -> None:
    """Select a database file, including temporary databases used by tests."""
    global DATABASE_PATH
    DATABASE_PATH = path


@contextlib.contextmanager
def _connection():
    """Open one connection per operation and enable foreign-key enforcement."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_schema() -> None:
    """Create the schema and apply migrations while preserving existing history."""
    with _connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        migrate_legacy_schema(connection)
        # execute() preserves the transaction; executescript() would commit it early.
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)

        for table, column, column_type in ADDED_COLUMNS:
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

        migrate_legacy_values(connection)


def register_audit(job_id, created_at, dpi, payment_slips_file, reference_file) -> None:
    with _connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO audit "
            "(job_id, created_at, status, dpi, payment_slips_file, reference_file, total_pages) "
            "VALUES (?, ?, 'processing', ?, ?, ?, NULL)",
            (job_id, created_at, dpi, payment_slips_file, reference_file),
        )


def update_status(job_id, status, total_pages=None) -> None:
    """Update the audit state and optionally the total page count."""
    with _connection() as connection:
        if total_pages is None:
            connection.execute("UPDATE audit SET status = ? WHERE job_id = ?", (status, job_id))
        else:
            connection.execute(
                "UPDATE audit SET status = ?, total_pages = ? WHERE job_id = ?",
                (status, total_pages, job_id),
            )


def save_page(job_id, row: dict) -> None:
    """Persist each processed page so an interrupted audit retains earlier results."""
    values = [job_id] + [row.get(label) for label, _column in _COLUMNS]
    columns = ", ".join(column for _label, column in _COLUMNS)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    with _connection() as connection:
        connection.execute(
            f"INSERT OR REPLACE INTO page (job_id, {columns}) VALUES (?, {placeholders})",
            values,
        )


def load_report(job_id) -> list[dict]:
    """Return ordered rows using the same keys as the stream and CSV."""
    columns = ", ".join(column for _label, column in _COLUMNS)
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT {columns} FROM page WHERE job_id = ? ORDER BY page", (job_id,)
        ).fetchall()

    return [
        {label: row[column] for label, column in _COLUMNS}
        for row in rows
    ]


_SUMMARY_QUERY = """
SELECT a.job_id,
       a.created_at,
       a.status,
       a.total_pages,
       COUNT(p.page)                                              AS processed_pages,
       COALESCE(SUM(p.overall_status = 'OK'), 0)                      AS matched,
       COALESCE(SUM(p.overall_status = 'ERROR'), 0)                    AS mismatched
  FROM audit a
  LEFT JOIN page p ON p.job_id = a.job_id
"""


def load_summary(job_id) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            _SUMMARY_QUERY + " WHERE a.job_id = ? GROUP BY a.job_id", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_audits(limit: int = 50) -> list[dict]:
    """List stored audits from newest to oldest."""
    with _connection() as connection:
        rows = connection.execute(
            _SUMMARY_QUERY + " GROUP BY a.job_id ORDER BY a.created_at DESC, a.rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
