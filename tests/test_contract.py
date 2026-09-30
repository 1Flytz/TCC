"""Verify the English contract across the engine, CLI, API, and static interface."""

import csv
import io
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import auditor
from api import main
from core import engine


def fake_pdf_pipeline(monkeypatch):
    """Keep the real audit generator while replacing external PDF/OCR tools."""
    monkeypatch.setattr(engine, "extract_reference_list", lambda _: [{"code": "113640", "amount": "76,82"}])
    monkeypatch.setattr(engine.pdf2image, "pdfinfo_from_path", lambda *args, **kwargs: {"Pages": 1})
    monkeypatch.setattr(engine.pdf2image, "convert_from_path", lambda *args, **kwargs: [Image.new("L", (10, 10))])
    monkeypatch.setattr(engine, "extract_image_fields", lambda _: {"code": "113640", "amount": "76,82"})
    monkeypatch.setattr(engine, "generate_annotated_image", lambda *args: {
        "image": "data:image/jpeg;base64,preview", "code_found": True, "amount_found": True,
    })


def test_upload_stream_history_and_csv_share_english_contract(monkeypatch):
    fake_pdf_pipeline(monkeypatch)
    main.AUDITS.clear()
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/v1/audits?dpi=300", files={
                "payment_slips": ("slips.pdf", b"%PDF-1.4", "application/pdf"),
                "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
            })
            assert response.status_code == 200
            job_id = response.json()["job_id"]
            stream = client.get(f"/api/v1/audits/{job_id}/events")
            events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
            assert [event["type"] for event in events] == ["start", "page", "end"]
            assert events[0]["dpi"] == 300
            assert events[0]["reference_count"] == 1
            assert events[1]["strategy"] == "1. Default"
            assert events[1]["row"]["Code (Reference PDF)"] == "113640"
            assert events[1]["row"]["Overall Status"] == "OK"
            assert events[2]["model"] == "Tesseract OCR"
            assert client.get(f"/api/v1/audits/{job_id}").json()["status"] == "completed"
            main.AUDITS.clear()
            assert client.get(f"/api/v1/audits/{job_id}").json()["matched"] == 1
            rows = client.get(f"/api/v1/audits/{job_id}/pages").json()
            assert rows == [events[1]["row"]]
            exported = client.get(f"/api/v1/audits/{job_id}/report.csv")
            assert list(csv.DictReader(io.StringIO(exported.text), delimiter=";"))[0]["Amount (OCR Slips)"] == "76,82"
            schema = client.get("/openapi.json").json()
            assert "/api/v1/audits/{job_id}/pages" in schema["paths"]
            assert set(schema["components"]["schemas"]["AuditCreated"]["properties"]) == {"job_id", "message"}
    finally:
        main.AUDITS.clear()


def test_english_cli_flags_export_the_same_report(monkeypatch, tmp_path):
    fake_pdf_pipeline(monkeypatch)
    output = tmp_path / "report.csv"
    monkeypatch.setattr("sys.argv", [
        "auditor.py", "--payment-slips", "slips.pdf", "--reference", "reference.pdf", "--output", str(output),
    ])
    auditor.main()
    with output.open(encoding="latin1", newline="") as file:
        rows = list(csv.DictReader(file, delimiter=";"))
    assert rows[0]["Code (OCR Slips)"] == "113640"
    assert rows[0]["Overall Status"] == "OK"


def test_static_dom_references_and_css_variables_are_consistent():
    static = Path(engine.BASE_DIR) / "static"
    html = (static / "index.html").read_text(encoding="utf-8-sig")
    script = (static / "app.js").read_text(encoding="utf-8-sig")
    css = (static / "style.css").read_text(encoding="utf-8-sig")
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "DOM IDs must be unique"
    assert set(re.findall(r'\$\("([^"]+)"\)', script)) <= set(ids)
    assert set(re.findall(r'var\((--[\w-]+)\)', css)) <= set(re.findall(r'(--[\w-]+)\s*:', css))
    assert '<html lang="en">' in html
    assert 'name="payment_slips"' in html
    assert 'name="reference"' in html
