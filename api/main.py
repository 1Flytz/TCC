"""Local REST API and static front end for PyConfer.

A worker thread runs blocking OCR and publishes page events to a bounded queue.
The SSE endpoint consumes those events. Start with uvicorn api.main:app --reload."""

import io
import json
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
import traceback

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import storage, engine

app = FastAPI(
    title="PyConfer API",
    version="1.0.0",
    description=(
        "Verify financial payment slips by comparing OCR registration codes "
        "and amounts against a reference PDF."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage.create_schema()

# Live audits are held in memory; older reports are read from SQLite.
AUDITS: dict[str, dict] = {}

_SENTINEL = object()

# Bound queues and retained audits to release large image payloads after use.
MAX_QUEUE_SIZE = 8           # events awaiting browser consumption
CONSUMER_TIMEOUT_SECONDS = 60         # full queue timeout before abandoning the audit
AUDIT_TTL_SECONDS = 60 * 60  # expire finished audits after one hour
MAX_AUDITS = 20               # maximum retained audits, excluding running jobs


class AuditCreated(BaseModel):
    job_id: str
    message: str


class AuditHistoryEntry(BaseModel):
    job_id: str
    created_at: str
    status: str
    total_pages: int | None
    processed_pages: int
    matched: int
    mismatched: int


class AuditSummary(BaseModel):
    job_id: str
    status: str
    processed_pages: int
    total_pages: int | None
    matched: int
    mismatched: int
    created_at: str


def _persist(operation, *args) -> None:
    """Log SQLite failures without interrupting live verification or CSV export."""
    try:
        operation(*args)
    except sqlite3.Error as e:
        print(f"[history] failed in {operation.__name__}: {e}")


def _drain_queue(event_queue: queue.Queue) -> None:
    """Release pending events and their annotated images."""
    while True:
        try:
            event_queue.get_nowait()
        except queue.Empty:
            return


def _publish_shutdown(event_queue: queue.Queue, item) -> None:
    """Publish a shutdown marker without blocking when the queue is full."""
    try:
        event_queue.put_nowait(item)
    except queue.Full:
        _drain_queue(event_queue)
        event_queue.put_nowait(item)


def _purge_old_audits() -> None:
    """Expire completed audits by age or count, preserving all running audits."""
    now = time.monotonic()
    finished_audits = sorted(
        (data["started_at"], job_id)
        for job_id, data in AUDITS.items()
        if data["status"] != "processing"
    )

    excess = max(0, len(AUDITS) - MAX_AUDITS)
    discard = {job_id for _started_at, job_id in finished_audits[:excess]}
    discard.update(job_id for start, job_id in finished_audits if now - start > AUDIT_TTL_SECONDS)

    for job_id in discard:
        AUDITS.pop(job_id, None)


def _execute_audit(job_id: str, payment_slips_path: str, reference_path: str, dpi: int, temp_directory: str):
    """Run verification in a worker thread and publish per-page events."""
    audit = AUDITS[job_id]
    event_queue = audit["event_queue"]
    try:
        for event in engine.stream_audit(payment_slips_path, reference_path, dpi=dpi):
            if event["type"] == "page":
                audit["report"].append(event["row"])
                if event["row"]["Overall Status"] == "OK":
                    audit["matched"] += 1
                elif event["row"]["Overall Status"] == "ERROR":
                    audit["mismatched"] += 1

                _persist(storage.save_page, job_id, event["row"])
            elif event["type"] == "start":
                audit["total_pages"] = event["total_pages"]
                _persist(storage.update_status, job_id, "processing", event["total_pages"])
            elif event["type"] == "end":
                # Publish the final event before closing the stream.
                audit["status"] = "completed"
                _publish_shutdown(event_queue, event)
                break # the final event has already been published

            try:
                # Publish start and page events with a consumer timeout.
                if event["type"] != "end":
                    event_queue.put(event, timeout=CONSUMER_TIMEOUT_SECONDS)
            except queue.Full:
                audit["status"] = "abandoned"
                _drain_queue(event_queue)
                return

        # Mark normal generator completion as completed.
        audit["status"] = "completed"
    except Exception as e:
        audit["status"] = "error"
        print("Critical error during audit execution:")
        traceback.print_exc()
        _publish_shutdown(event_queue, {"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        # Persist the final state and release the queue consumer and temporary files.
        _persist(storage.update_status, job_id, audit["status"])
        _publish_shutdown(event_queue, _SENTINEL)
        shutil.rmtree(temp_directory, ignore_errors=True)


@app.post("/api/v1/audits", response_model=AuditCreated, tags=["Audit"])
async def create_audit(
    payment_slips: UploadFile = File(..., description="PDF containing scanned payment slips."),
    reference: UploadFile = File(..., description="Reference PDF containing the master list."),
    dpi: int = 500,
):
    """Upload both PDFs and start a background audit.

    Returns a job ID immediately. Reference and page counts arrive in the first
    stream event, avoiding a redundant reference-PDF read in this request."""
    for file in (payment_slips, reference):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"'{file.filename}' is not a PDF.")

    temp_directory = tempfile.mkdtemp(prefix="pyconfer_")
    payment_slips_path = f"{temp_directory}/payment_slips.pdf"
    reference_path = f"{temp_directory}/reference.pdf"

    for file, destination in ((payment_slips, payment_slips_path), (reference, reference_path)):
        with open(destination, "wb") as output:
            shutil.copyfileobj(file.file, output)

    _purge_old_audits()

    job_id = uuid.uuid4().hex[:12]
    AUDITS[job_id] = {
        "event_queue": queue.Queue(maxsize=MAX_QUEUE_SIZE),
        "report": [],
        "status": "processing",
        "total_pages": None,
        "matched": 0,
        "mismatched": 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        # Use a monotonic clock for age calculations.
        "started_at": time.monotonic(),
    }
    _persist(
        storage.register_audit,
        job_id, AUDITS[job_id]["created_at"], dpi, payment_slips.filename, reference.filename,
    )

    threading.Thread(
        target=_execute_audit,
        args=(job_id, payment_slips_path, reference_path, dpi, temp_directory),
        daemon=True,
    ).start()

    return AuditCreated(job_id=job_id, message="Audit started. Follow the events endpoint.")


@app.get("/api/v1/audits", response_model=list[AuditHistoryEntry], tags=["Audit"])
def list_audits(limit: int = 50):
    """List stored audits from newest to oldest."""
    return [AuditHistoryEntry(**record) for record in storage.list_audits(limit)]


@app.get("/api/v1/audits/{job_id}/events", tags=["Audit"])
def stream_audit_events(job_id: str):
    """Stream JSON events with type start, page, error, or end over SSE."""
    audit = AUDITS.get(job_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found.")

    def generate_events():
        while True:
            event = audit["event_queue"].get()
            if event is _SENTINEL:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/audits/{job_id}", response_model=AuditSummary, tags=["Audit"])
def get_audit(job_id: str):
    """Return live progress, falling back to persisted history after expiration."""
    audit = AUDITS.get(job_id)
    if audit:
        return AuditSummary(
            job_id=job_id,
            status=audit["status"],
            processed_pages=len(audit["report"]),
            total_pages=audit["total_pages"],
            matched=audit["matched"],
            mismatched=audit["mismatched"],
            created_at=audit["created_at"],
        )

    record = storage.load_summary(job_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audit not found.")
    return AuditSummary(**record)


@app.get("/api/v1/audits/{job_id}/pages", tags=["Audit"])
def list_pages(job_id: str):
    """Return report rows for live or historical audits. Images are not persisted."""
    audit = AUDITS.get(job_id)
    if audit:
        return audit["report"]

    if not storage.load_summary(job_id):
        raise HTTPException(status_code=404, detail="Audit not found.")
    return storage.load_report(job_id)


@app.get("/api/v1/audits/{job_id}/report.csv", tags=["Audit"])
def download_report(job_id: str):
    """Download CSV rows from memory or stored history."""
    audit = AUDITS.get(job_id)
    if audit:
        report = audit["report"]
    else:
        if not storage.load_summary(job_id):
            raise HTTPException(status_code=404, detail="Audit not found.")
        report = storage.load_report(job_id)

    if not report:
        raise HTTPException(status_code=409, detail="No pages processed yet.")

    buffer = io.StringIO()
    pd.DataFrame(report, columns=engine.REPORT_COLUMNS).to_csv(buffer, index=False, sep=";")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Report_{job_id}.csv"'},
    )


class RevalidatedStaticFiles(StaticFiles):
    """Require static-file revalidation so browser code follows local edits.

    The no-cache directive allows storage but requires revalidation before reuse."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serve static assets directly with FastAPI; no build step is required.
app.mount("/", RevalidatedStaticFiles(directory=f"{engine.BASE_DIR}/static", html=True), name="static")
