"""Test API validation, streaming lifecycle, and memory limits."""

import queue
import time

import pytest
from fastapi.testclient import TestClient

from api import main
from core import storage


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the global in-memory audit registry between tests."""
    main.AUDITS.clear()
    yield
    main.AUDITS.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _audit(status, age_seconds=0.0, **extras):
    return {
        "status": status,
        "started_at": time.monotonic() - age_seconds,
        **extras,
    }


# ==========================================
# Queue cleanup
# ==========================================


def test_draining_queue_releases_pending_events():
    event_queue = queue.Queue(maxsize=4)
    for number in range(4):
        event_queue.put_nowait({"page": number, "image": "x" * 130_000})

    main._drain_queue(event_queue)

    assert event_queue.empty()


def test_shutdown_does_not_block_on_full_queue():
    """Shutdown does not block on full queue."""
    event_queue = queue.Queue(maxsize=3)
    for number in range(3):
        event_queue.put_nowait(number)

    start = time.monotonic()
    main._publish_shutdown(event_queue, main._SENTINEL)
    assert time.monotonic() - start < 1, "must not block waiting for space"

    assert event_queue.get_nowait() is main._SENTINEL
    assert event_queue.empty()


# ==========================================
# Registry expiration
# ==========================================


def test_finished_audit_expires_by_age():
    main.AUDITS.update({
        "old": _audit("completed", main.AUDIT_TTL_SECONDS + 60),
        "recent": _audit("completed"),
    })

    main._purge_old_audits()

    assert set(main.AUDITS) == {"recent"}


@pytest.mark.parametrize("status", ["completed", "error", "abandoned"])
def test_all_finished_states_expire(status):
    main.AUDITS["oldest"] = _audit(status, main.AUDIT_TTL_SECONDS + 60)

    main._purge_old_audits()

    assert main.AUDITS == {}


def test_running_audit_is_never_discarded():
    """Running audit is never discarded."""
    main.AUDITS["running"] = _audit("processing", age_seconds=999_999)

    main._purge_old_audits()

    assert "running" in main.AUDITS


def test_registry_limit_keeps_recent_audits():
    # Future timestamps isolate count limits from TTL expiration.
    for number in range(main.MAX_AUDITS + 10):
        main.AUDITS[f"job{number:03d}"] = _audit("completed", age_seconds=-number)

    main._purge_old_audits()

    assert len(main.AUDITS) == main.MAX_AUDITS
    assert "job000" not in main.AUDITS, "oldest audit should have been removed"
    assert f"job{main.MAX_AUDITS + 9:03d}" in main.AUDITS, "newest audit should remain"


def test_registry_limit_preserves_running_audits():
    for number in range(main.MAX_AUDITS + 10):
        main.AUDITS[f"live{number:03d}"] = _audit("processing", age_seconds=-number)

    main._purge_old_audits()

    assert len(main.AUDITS) == main.MAX_AUDITS + 10


# ==========================================
# Abandoned browser sessions
# ==========================================


def test_audit_without_consumer_is_abandoned(monkeypatch, tmp_path):
    """Audit without consumer is abandoned."""
    monkeypatch.setattr(main, "CONSUMER_TIMEOUT_SECONDS", 0.2)

    def fake_engine(*_args, **_kwargs):
        yield {"type": "start", "total_pages": 500, "reference_count": 500}
        for number in range(1, 501):
            yield {
                "type": "page",
                "page": number,
                "row": {"Overall Status": "OK"},
                "image": "x" * 130_000,
            }

    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)

    main.AUDITS["orphan"] = _audit(
        "processing",
        event_queue=queue.Queue(maxsize=main.MAX_QUEUE_SIZE),
        report=[],
        total_pages=None,
        matched=0,
        mismatched=0,
        created_at="",
    )
    temp_directory = tmp_path / "job"
    temp_directory.mkdir()

    main._execute_audit("orphan", "payment_slips.pdf", "reference.pdf", 200, str(temp_directory))

    audit = main.AUDITS["orphan"]
    assert audit["status"] == "abandoned"
    assert len(audit["report"]) < 500, "should stop well before the end"
    assert audit["event_queue"].get_nowait() is main._SENTINEL
    assert audit["event_queue"].empty(), "pending images should have been released"
    assert not temp_directory.exists(), "temporary directory should have been removed"


# ==========================================
# Endpoints
# ==========================================


@pytest.mark.parametrize("path", [
    "/api/v1/audits/missing",
    "/api/v1/audits/missing/events",
    "/api/v1/audits/missing/report.csv",
])
def test_unknown_job_returns_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Audit not found."


def test_post_returns_only_job_id_and_message(client):
    """Post returns only job id and message."""
    response = client.post(
        "/api/v1/audits",
        files={
            "payment_slips": ("payment_slips.pdf", b"%PDF-1.4", "application/pdf"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {"job_id", "message"}


def test_non_pdf_upload_is_rejected(client):
    response = client.post(
        "/api/v1/audits",
        files={
            "payment_slips": ("spreadsheet.xlsx", b"not a pdf", "application/vnd.ms-excel"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert "spreadsheet.xlsx" in response.json()["detail"]


def test_report_before_first_page_returns_409(client):
    main.AUDITS["newly_created"] = _audit("processing", report=[])

    response = client.get("/api/v1/audits/newly_created/report.csv")

    assert response.status_code == 409


def test_expired_audit_is_available_from_history(client):
    """Expired audit is available from history."""
    storage.register_audit("old", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    storage.update_status("old", "completed", 2)
    for page, statusText in ((1, "OK"), (2, "ERROR")):
        storage.save_page("old", {
            "Page": page,
            "Code (Reference PDF)": "113640",
            "Code (OCR Slips)": "113640",
            "Code Status": "OK",
            "Amount (Reference PDF)": "76,82",
            "Amount (OCR Slips)": "76,82",
            "Amount Status": "OK",
            "Overall Status": statusText,
        })

    assert "old" not in main.AUDITS, "audit must be absent from memory"

    summary = client.get("/api/v1/audits/old").json()
    assert summary["status"] == "completed"
    assert summary["processed_pages"] == 2
    assert summary["matched"] == 1
    assert summary["mismatched"] == 1

    csv = client.get("/api/v1/audits/old/report.csv")
    assert csv.status_code == 200
    assert "113640" in csv.text


def test_old_audit_pages_come_from_history(client):
    """Old audit pages come from history."""
    storage.register_audit("past", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    storage.save_page("past", {
        "Page": 47,
        "Code (Reference PDF)": "116110",
        "Code (OCR Slips)": "716110",
        "Code Status": "MISMATCH",
        "Amount (Reference PDF)": "76,82",
        "Amount (OCR Slips)": "76,82",
        "Amount Status": "OK",
        "Overall Status": "ERROR",
    })

    rows = client.get("/api/v1/audits/past/pages").json()

    assert len(rows) == 1
    assert rows[0]["Page"] == 47
    assert rows[0]["Code (OCR Slips)"] == "716110"
    assert "image" not in rows[0], "annotated images are not persisted"


def test_live_audit_pages_come_from_memory(client):
    main.AUDITS["live"] = _audit("processing", report=[{"Page": 1}])

    assert client.get("/api/v1/audits/live/pages").json() == [{"Page": 1}]


def test_unknown_job_pages_return_404(client):
    assert client.get("/api/v1/audits/missing/pages").status_code == 404


def test_front_end_requires_cache_revalidation(client):
    """Front end requires cache revalidation."""
    response = client.get("/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_history_lists_audits(client):
    storage.register_audit("j1", "2026-08-10T09:00:00", 500, "b.pdf", "c.pdf")
    storage.register_audit("j2", "2026-08-15T09:00:00", 300, "b.pdf", "c.pdf")

    history = client.get("/api/v1/audits").json()

    assert [item["job_id"] for item in history] == ["j2", "j1"]


def test_memory_takes_priority_over_history(client):
    """Memory takes priority over history."""
    storage.register_audit("live", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    main.AUDITS["live"] = _audit(
        "processing",
        report=[{"Page": 1}],
        total_pages=50,
        matched=1,
        mismatched=0,
        created_at="2026-08-15T18:00:00",
    )

    summary = client.get("/api/v1/audits/live").json()

    assert summary["processed_pages"] == 1, "must not use the database that has no pages yet"
    assert summary["total_pages"] == 50


def test_summary_reflects_progress(client):
    main.AUDITS["ongoing"] = _audit(
        "processing",
        report=[{"Page": 1}, {"Page": 2}],
        total_pages=50,
        matched=2,
        mismatched=0,
        created_at="2026-08-15T18:43:51",
    )

    body = client.get("/api/v1/audits/ongoing").json()

    assert body["processed_pages"] == 2
    assert body["total_pages"] == 50
    assert body["matched"] == 2
    assert body["status"] == "processing"
