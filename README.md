# PyConfer

**OCR-assisted verification of financial payment slips.**

PyConfer is an undergraduate final-year project (TCC) that compares registration codes and amounts from scanned payment slips (RLC / financial forms) against a reference PDF. It turns a page-by-page manual check into a batch report, with visual evidence showing where each field was read.

The current application is a **local, single-user prototype** built with Python, FastAPI, SQLite, and plain JavaScript. Source identifiers, documentation, the interface, API fields, and report labels are in English. Input documents still follow the original Brazilian registration and monetary formats.

## Features

- Upload a scanned payment-slip PDF and a reference PDF through the web interface.
- Follow results page by page through Server-Sent Events (SSE).
- Inspect annotated previews highlighting the registration code and amount.
- Compare expected and extracted values, filter discrepancies, and inspect their categories.
- Choose 200, 300, or 500 DPI and see the active resolution during processing.
- Track elapsed time with the browser's **Reading time** counter.
- Reopen previous audit reports stored in SQLite and download CSV results.
- Run the same verification engine from the command line.

The timer starts when the browser begins following an audit and stops when the interface closes that session. It is not an isolated OCR benchmark and is not saved in the audit history.

## How verification works

1. **Read the reference list.** `pypdf` extracts text from the reference PDF and identifies registration codes and amounts.
2. **Render each scanned page.** `pdf2image` and Poppler convert payment slips into images one page at a time.
3. **Extract the fields.** Tesseract reads a preprocessed image. If the initial code and amount match the expected values, that reading is used immediately.
4. **Build a consensus when needed.** If the initial reading does not match, the engine runs the remaining seven strategies: a different threshold, sharpening, the original image, 2x zoom, a higher threshold, contrast adjustment, and stroke thickening. The most frequent nonempty code and amount are selected independently. This is a frequency vote, not a calibrated confidence score.
5. **Compare and explain.** The engine compares the relevant code suffix exactly, compares normalized amounts, and classifies discrepancies. OCR coordinates are mapped back to the original page for the annotated preview.

Code comparison deliberately does not tolerate substitutions such as `6`/`8` or `0`/`9`: different registrations may differ by precisely those digits. A mismatch is presented for human review instead of being silently accepted.

## Discrepancy categories

| Report value | Meaning | Suggested review |
|---|---|---|
| `OTHER REGISTRATION` | The extracted code belongs to another registration in the reference list. | Check whether the wrong slip was included or OCR misread the code. |
| `UNREAD` | The required field was not extracted. | Inspect scan quality and consider rescanning or increasing DPI. |
| `REVIEW` | A field differs without meeting the categories above. | Compare the highlighted reading with the original slip. |

These categories help prioritize review; they do not prove whether a discrepancy comes from the document or the OCR. Overall report statuses are `OK`, `ERROR` (mismatch), and `END OF LIST` (no usable expected registration for that position).

## Requirements

- **Python 3.10 or later**; compatibility also depends on the pinned packages in [requirements.txt](requirements.txt).
- **Git** to clone the repository.
- **Tesseract OCR**, including its English language data (`eng`).
- **Poppler**, including its PDF information and rendering utilities.

Tesseract and Poppler are external programs, not Python packages. Install them separately. Windows installation resources: [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler binaries](https://github.com/oschwartz10612/poppler-windows/releases/).

## Installation

```bash
git clone https://github.com/1Flytz/TCC.git
cd TCC
python -m venv .venv
```

Activate the environment using the command for your shell:

| Shell | Command |
|---|---|
| Windows PowerShell | `.\.venv\Scripts\Activate.ps1` |
| Windows Command Prompt | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

```bash
python -m pip install -r requirements.txt
```

Create the virtual environment on your own machine; environments copied from another computer may contain paths to an unavailable Python installation.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Tesseract executable. |
| `POPPLER_PATH` | `C:\poppler\Library\bin` | Directory containing Poppler utilities. |
| `PYCONFER_DB` | `pyconfer.db` in the project root | SQLite audit-history file. |

For custom Windows locations, set these before starting the application:

```powershell
$env:TESSERACT_CMD = "C:\tools\Tesseract-OCR\tesseract.exe"
$env:POPPLER_PATH = "C:\tools\poppler\Library\bin"
$env:PYCONFER_DB = "C:\data\pyconfer.db"
```

Ensure the database's parent directory exists. If the configured Tesseract file or Poppler directory does not exist, the engine falls back to utilities on the system `PATH`. On macOS and Linux, utilities installed on `PATH` are used through this fallback; use your shell's `export` syntax for custom environment variables. The application reads environment variables directly; it does not load `.env` files.

## Using the web application

```bash
python -m uvicorn api.main:app --reload
```

Open [localhost:8000](http://localhost:8000), select both PDFs, choose a resolution, and click **Start audit**. The results panel displays progress, active DPI, elapsed time, and matching and mismatching page counts. Select a result to inspect the expected values and annotated image. Download the CSV when processing ends.

The default is **500 DPI**. Lower resolutions are available for faster runs, but may make similar digits harder to distinguish. The appropriate setting depends on scan quality; 500 DPI is not a guarantee of error-free recognition.

Keep the audit page open while processing. The live stream uses a bounded queue; if it stays full for 60 seconds without consumption, the worker marks the audit as abandoned. Previously saved rows remain in the history.

## Using the command line

```bash
python auditor.py --payment-slips docs/payment_slips.pdf --reference docs/reference.pdf
```

| Option | Purpose | Default |
|---|---|---|
| `--payment-slips` | Scanned payment-slip PDF. | `docs/payment_slips.pdf` in the project root |
| `--reference` | Reference PDF. | `docs/reference.pdf` in the project root |
| `--output` | Output CSV path. | `Final_Report.csv` in the project root |
| `--dpi` | Rendering resolution. | `500` |
| `--tesseract` | Tesseract executable. | Configured engine value |
| `--poppler` | Poppler binary directory. | Configured engine value |

The CLI runs the same engine without annotated previews and writes a semicolon-separated CSV using Latin-1 encoding. Web CSV exports also use semicolons, with UTF-8 text. The CLI does not save audits to SQLite; persistence is handled by the API layer.

## Input format and current limitations

The parser targets the project's reference document layout; it is not a general parser for arbitrary invoices or payment slips.

- The reference PDF must contain extractable text. It is not processed with OCR.
- Reference codes follow a pattern of four or five digits followed by `-0`.
- Amount extraction expects two or three integer digits and two decimal digits; the reference parser expects a comma decimal separator.
- Codes and amounts are paired by their order within each reference page, and payment-slip pages are matched to the resulting list by position. Extra totals, missing entries, or a different document order can misalign comparisons.
- Scanned code extraction expects a 16-digit numeric sequence that becomes a four-to-six-digit code ending in zero after leading zeros are removed.
- Missing or inaccurate OCR coordinates can prevent a field from being highlighted, even when a reading is available.

PDFs are excluded from version control because working documents contain taxpayer data. Supply your own PDFs through the interface, or create a local `docs/` folder for CLI inputs. The clone does not include the reference dataset.

## API

Interactive API documentation is available at [localhost:8000/docs](http://localhost:8000/docs) while the server is running. Routes, upload fields, JSON keys, and status values use English names.

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/audits` | Upload multipart fields `payment_slips` and `reference`; optional `dpi` query parameter defaults to 500. Returns `job_id` and `message`. |
| `GET` | `/api/v1/audits` | List saved audits, newest first; `limit` defaults to 50. |
| `GET` | `/api/v1/audits/{job_id}/events` | Consume the live SSE stream. |
| `GET` | `/api/v1/audits/{job_id}` | Retrieve audit status and counts. |
| `GET` | `/api/v1/audits/{job_id}/pages` | Retrieve report rows as JSON, including historical audits. |
| `GET` | `/api/v1/audits/{job_id}/report.csv` | Download the available report rows as CSV. |

Each SSE message contains a JSON object with a `type` field:

| `type` | Content |
|---|---|
| `start` | `total_pages`, `reference_count`, and `dpi`. |
| `page` | Page number, total page count, and report row in `row`; live previews may include `image`, `strategy`, `code_found`, and `amount_found`. |
| `error` | Error description in `message`. |
| `end` | `total_pages` and `model`, currently `"Tesseract OCR"`. |

Audit states are `processing`, `completed`, `error`, and `abandoned`. The browser currently displays resolution from `start.dpi`; it does not display `end.model`. SSE is a live consumption channel, not a persisted event replay or broadcast system.

The prototype has no authentication or user permissions. Its CORS middleware is configured with wildcard origins, methods, and headers, with credentials enabled. This configuration does not provide access control; the intended runtime is local.

## Persistence and history

SQLite stores audit metadata and report rows in the `audit` and `page` tables. The API saves each processed page, allowing earlier results to survive an interruption or server restart. Old reports can be reopened and exported even after their live in-memory entry is removed.

Annotated images, original uploaded PDFs, the extracted reference list, and the browser timer are not retained in the database. Temporary uploads are removed when the worker exits normally or through its cleanup handler. Historical views therefore contain report data without the live annotated images.

Persisted partial results are not an automatic resume mechanism. An abrupt server shutdown can leave an audit marked as processing; restart recovery is not implemented.

## Upgrading from the Portuguese codebase

This refactor changes the public names used by scripts and API clients. The bundled
web interface uses the new contract. External integrations must update their routes,
upload fields, JSON keys, status comparisons, and CSV column names; the previous
API names and command-line flags are not aliases.

| Previous name | Current name |
|---|---|
| `conferidor.py` | `auditor.py` |
| `core/armazenamento.py` | `core/storage.py` |
| `--boletos`, `--consulta`, `--saida` | `--payment-slips`, `--reference`, `--output` |
| `/api/v1/auditorias` | `/api/v1/audits` |
| `/eventos`, `/paginas`, `/relatorio.csv` | `/events`, `/pages`, `/report.csv` |
| Upload fields `boletos`, `consulta` | `payment_slips`, `reference` |
| Event keys `tipo`, `linha`, `imagem` | `type`, `row`, `image` |
| Event types `inicio`, `pagina`, `erro`, `fim` | `start`, `page`, `error`, `end` |
| `Relatorio_Final_Python.csv` | `Final_Report.csv` |

Before starting the new server against an existing database, stop the old server
and back up the database. Startup migrates the legacy tables, columns, statuses,
and discrepancy categories in one transaction. Existing audit IDs, timestamps,
document filenames, codes, and monetary values are preserved. Repeated startup is
safe; conflicting old and new schemas stop the migration instead of merging or
discarding records. Older versions must use the backup rather than the migrated file.

Legacy Portuguese strings remain in `core/migrations.py` and its tests solely to
identify existing data. Input PDFs are not renamed: select them in the interface or
pass their existing paths explicitly to the CLI. Brazilian decimal separators and
registration-code rules are unchanged by the English translation.

## Tests

```bash
python -m pytest
```

The suite covers comparison rules, normalization, discrepancy classification, reference parsing, API validation and lifecycle behavior, and SQLite persistence. Tests use synthetic or mocked inputs and a temporary database; they do not require the private PDFs or an installed OCR toolchain. The test configuration always selects a disposable database, even if `PYCONFER_DB` is set.

These tests do not establish OCR accuracy on real scans or validate the full browser workflow. Those require a separate run with suitable local documents.

If Node.js is available, run the front-end contract smoke test with
`node --test tests/frontend.test.cjs`. It executes the browser script against a
simulated DOM and event stream without third-party packages. Node.js is optional
for development checks and is not required to run PyConfer.

## Project structure

```text
TCC/
├── core/
│   ├── engine.py          # PDF extraction, OCR, consensus, and visual evidence
│   ├── storage.py         # SQLite audit history
│   └── migrations.py      # Upgrade legacy history to the English schema
├── api/
│   └── main.py            # REST API, worker lifecycle, SSE, and static files
├── static/                # HTML, CSS, and plain JavaScript; no build step
├── tests/                 # Engine, API, and persistence tests
├── docs/                  # Optional local PDF inputs; not included in the clone
├── auditor.py             # Command-line entry point
├── requirements.txt       # Pinned Python dependencies
├── pytest.ini             # Test configuration
├── CONTRIBUTING.md        # Contribution workflow and team roadmap
└── README.md
```

Both the CLI and API consume the engine's event generator. The API owns persistence and exposes the JSON contract consumed by the browser. The engine does not depend on FastAPI, SQLite, or the front end.

## Contributing and next steps

See [CONTRIBUTING.md](CONTRIBUTING.md) for team responsibilities and the next phase: authentication, shared storage, metrics, and a recorded human-review workflow. These are planned capabilities, not features of the current prototype.
