"""Test SQLite audit history and report persistence."""

import pytest

from core import storage, engine


def _row(page, overall_status="OK", code="113640", amount="76,82", category=""):
    """Build a report row using the engine contract."""
    return {
        "Page": page,
        "Code (Reference PDF)": code,
        "Code (OCR Slips)": code,
        "Code Status": "OK",
        "Amount (Reference PDF)": amount,
        "Amount (OCR Slips)": amount,
        "Amount Status": "OK",
        "Overall Status": overall_status,
        "Category": category,
    }


@pytest.fixture
def registered_audit():
    storage.register_audit("job1", "2026-08-15T18:00:00", 500, "payment_slips.pdf", "reference.pdf")
    return "job1"


def test_new_audit_has_no_pages(registered_audit):
    summary = storage.load_summary(registered_audit)

    assert summary["status"] == "processing"
    assert summary["processed_pages"] == 0
    assert summary["matched"] == 0
    assert summary["mismatched"] == 0
    assert summary["total_pages"] is None


def test_report_uses_engine_format(registered_audit):
    """Report uses engine format."""
    storage.save_page(registered_audit, _row(1))

    report = storage.load_report(registered_audit)

    assert len(report) == 1
    assert set(report[0]) == set(engine.REPORT_COLUMNS)
    assert report[0] == _row(1)


def test_pages_are_returned_in_order(registered_audit):
    for page in (3, 1, 2):
        storage.save_page(registered_audit, _row(page))

    report = storage.load_report(registered_audit)

    assert [row["Page"] for row in report] == [1, 2, 3]


def test_rewriting_page_does_not_duplicate_it(registered_audit):
    """Rewriting page does not duplicate it."""
    storage.save_page(registered_audit, _row(1, overall_status="ERROR"))
    storage.save_page(registered_audit, _row(1, overall_status="OK"))

    report = storage.load_report(registered_audit)

    assert len(report) == 1
    assert report[0]["Overall Status"] == "OK"


def test_summary_counts_matches_and_mismatches(registered_audit):
    storage.save_page(registered_audit, _row(1, "OK"))
    storage.save_page(registered_audit, _row(2, "OK"))
    storage.save_page(registered_audit, _row(3, "ERROR"))
    storage.save_page(registered_audit, _row(4, "END OF LIST"))

    summary = storage.load_summary(registered_audit)

    assert summary["processed_pages"] == 4
    assert summary["matched"] == 2
    assert summary["mismatched"] == 1


def test_status_update_preserves_page_count(registered_audit):
    storage.update_status(registered_audit, "processing", 50)
    storage.update_status(registered_audit, "completed")

    summary = storage.load_summary(registered_audit)

    assert summary["status"] == "completed"
    assert summary["total_pages"] == 50, "later updates must preserve the total"


def test_unknown_audit_returns_none():
    assert storage.load_summary("missing") is None
    assert storage.load_report("missing") == []


def test_history_is_newest_first():
    for number, timestamp in enumerate(["2026-08-10T09:00:00", "2026-08-15T09:00:00", "2026-08-12T09:00:00"]):
        storage.register_audit(f"job{number}", timestamp, 500, "b.pdf", "c.pdf")

    history = storage.list_audits()

    assert [item["job_id"] for item in history] == ["job1", "job2", "job0"]


def test_history_respects_limit():
    for number in range(10):
        storage.register_audit(f"job{number}", f"2026-08-{number + 1:02d}T09:00:00", 500, "b.pdf", "c.pdf")

    assert len(storage.list_audits(limit=3)) == 3


def test_deleting_audit_cascades_to_pages(registered_audit):
    """Deleting audit cascades to pages."""
    storage.save_page(registered_audit, _row(1))

    with storage._connection() as connection:
        connection.execute("DELETE FROM audit WHERE job_id = ?", (registered_audit,))

    assert storage.load_report(registered_audit) == []
