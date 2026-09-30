"""PyConfer OCR engine.

The CLI and API share the same reference extraction, consensus, and audit stream.
The engine is independent of persistence and HTTP interfaces."""

import base64
import io
import os
import re
from collections import Counter

import pdf2image
import pypdf
import pytesseract
from PIL import ImageDraw, ImageEnhance, ImageFilter

# ==========================================
# External tool and input paths
# ==========================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYMENT_SLIPS_PATH = os.path.join(BASE_DIR, 'docs', 'payment_slips.pdf')
REFERENCE_PDF_PATH = os.path.join(BASE_DIR, 'docs', 'reference.pdf')

# Override tool locations through environment variables or configure_paths.
TESSERACT_PATH = os.environ.get('TESSERACT_CMD', r"C:\Program Files\Tesseract-OCR\tesseract.exe")
POPPLER_PATH = os.environ.get('POPPLER_PATH', r"C:\poppler\Library\bin")

DEFAULT_DPI = 500
TESSERACT_CONFIG = r'--psm 6 -c tessedit_char_whitelist=0123456789,. -c classify_bln_numeric_mode=1 -c tessedit_char_blacklist=IlOo'


def configure_paths(tess_path: str, poppler_bin: str):
    """Configure external tools, falling back to executables on PATH when needed."""
    global TESSERACT_PATH, POPPLER_PATH
    TESSERACT_PATH = tess_path
    POPPLER_PATH = poppler_bin
    pytesseract.pytesseract.tesseract_cmd = tess_path if os.path.isfile(tess_path) else 'tesseract'
    if POPPLER_PATH and os.path.isdir(POPPLER_PATH) and POPPLER_PATH not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + POPPLER_PATH


# Configure tools at import time for both API and CLI execution.
configure_paths(TESSERACT_PATH, POPPLER_PATH)


# ==========================================
# Field extraction and comparison
# ==========================================

def extract_reference_list(reference_path):
    """Read reference codes and amounts using positional pairing within each page."""
    print("Reading codes and amounts from the reference PDF...")
    entries = []
    try:
        reader = pypdf.PdfReader(reference_path)
        for page in reader.pages:
            text = page.extract_text()
            if not text: continue

            raw_codes = re.findall(r'\b(\d{4,5})-0\b', text)
            raw_amounts = re.findall(r'\b\d{2,3},\d{2}\b', text)
            max_len = max(len(raw_codes), len(raw_amounts))

            for k in range(max_len):
                registration_code = raw_codes[k] + "0" if k < len(raw_codes) else "N/A"
                amount = raw_amounts[k] if k < len(raw_amounts) else "N/A"
                entries.append({"code": registration_code, "amount": amount})
    except Exception as e:
        print(f"Failed to read reference PDF: {e}")
    return entries


def _comparable_suffix(code: str) -> str:
    """Extract the five- or six-digit registration suffix ending in zero."""
    for length in (6, 5):
        suffix = code[-length:]
        if len(suffix) == length and suffix.endswith("0"):
            return suffix
    return ""


def code_status(read_code: str, expected_code: str):
    """Compare registration suffixes exactly.

    Digit substitutions can identify a different real registration, so OCR ambiguity
    must remain visible for human review instead of being silently accepted."""
    if not read_code or not expected_code or expected_code == "N/A":
        return "MISMATCH"

    read_suffix = _comparable_suffix(read_code)
    expected_suffix = _comparable_suffix(expected_code)

    return "OK" if (read_suffix and read_suffix == expected_suffix) else "MISMATCH"


def normalize_amount(extracted_text: str):
    """Normalize supported OCR decimal separators to the report format: 123,45."""
    if not extracted_text: return None
    normalized_text = extracted_text.replace('‚', ',').replace('’', ',').replace('`', ',').replace('´', ',').replace('·', '.')
    amount_match = re.search(r'\b\d{2,3}[.,]\d{2}\b', normalized_text)
    if amount_match:
        amount = amount_match.group(0).replace('.', ',')
        if int(amount.split(',')[0]) <= 999: return amount
    return None


def extract_image_fields(img_param):
    """Extract a registration code and amount using Tesseract."""
    result = {"code": None, "amount": None}

    try:
        text = pytesseract.image_to_string(img_param, lang='eng', config=TESSERACT_CONFIG)
        code_match = re.search(r'\b\d{16}\b', text)
        if code_match:
            extracted_code = str(int(code_match.group(0)))
            if 4 <= len(extracted_code) <= 6 and extracted_code.endswith('0'):
                result["code"] = extracted_code

        amount = normalize_amount(text)
        if amount: result["amount"] = amount
    except Exception:
        pass
    return result


# ==========================================
# Image preprocessing and consensus
# ==========================================

# Keep strategies reusable so winning fields can be located for previews.
STRATEGIES = [
    ("1. Default", lambda img: img.point(lambda x: 0 if x < 140 else 255, '1')),
    ("2. Dark", lambda img: img.point(lambda x: 0 if x < 180 else 255, '1')),
    ("3. Sharpness", lambda img: ImageEnhance.Sharpness(img).enhance(2.5).point(lambda x: 0 if x < 160 else 255, '1')),
    ("4. Original", lambda img: img),
    ("5. Zoom 2x", lambda img: img.resize((img.width * 2, img.height * 2)).point(lambda x: 0 if x < 160 else 255, '1')),
    ("6. High Threshold", lambda img: img.point(lambda x: 0 if x < 210 else 255, '1')),
    ("7. Contrast", lambda img: ImageEnhance.Contrast(img).enhance(3.0).convert('1')),
    ("8. Thicken", lambda img: img.filter(ImageFilter.MinFilter(3)).point(lambda x: 0 if x < 140 else 255, '1')),
]


def process_page_with_consensus(img_gray, expected_code, expected_amount):
    """Vote across eight image strategies when the first reading differs.

    Track the strategy producing each winning field to locate its visual evidence."""

    # Track the first strategy producing each candidate field.
    code_sources, amount_sources = {}, {}

    # Try the default strategy first.
    initial_image = STRATEGIES[0][1](img_gray)
    initial_reading = extract_image_fields(initial_image)

    final_code = initial_reading["code"]
    final_amount = initial_reading["amount"]
    if final_code: code_sources.setdefault(final_code, 0)
    if final_amount: amount_sources.setdefault(final_amount, 0)

    # Run the remaining strategies only when the first reading differs.
    if not (final_code == expected_code and final_amount == expected_amount):
        code_candidates, amount_candidates = [], []
        if final_code: code_candidates.append(final_code)
        if final_amount: amount_candidates.append(final_amount)

        for index, (name, apply_filter) in enumerate(STRATEGIES[1:], start=1):
            try:
                processed_image = apply_filter(img_gray)
                readings = extract_image_fields(processed_image)
                if readings["code"]:
                    code_candidates.append(readings["code"])
                    code_sources.setdefault(readings["code"], index)
                if readings["amount"]:
                    amount_candidates.append(readings["amount"])
                    amount_sources.setdefault(readings["amount"], index)
            except Exception:
                pass

        final_code = Counter(code_candidates).most_common(1)[0][0] if code_candidates else "0"
        final_amount = Counter(amount_candidates).most_common(1)[0][0] if amount_candidates else "0,00"

    code_check = code_status(final_code, expected_code)
    amount_check = "OK" if final_amount == expected_amount else "MISMATCH"
    overall_status = "ERROR" if (code_check != "OK" or amount_check != "OK") else "OK"
    if expected_code == "N/A": overall_status = "END OF LIST"

    return {
        "code": final_code,
        "amount": final_amount,
        "code_status": code_check,
        "amount_status": amount_check,
        "overall_status": overall_status,
        "code_strategy": code_sources.get(final_code),
        "amount_strategy": amount_sources.get(final_amount),
    }


# ==========================================
# Locate and highlight OCR evidence
# ==========================================

def _matches(word: str, target: str, field_type: str) -> bool:
    """Check whether a Tesseract word matches the extracted field."""
    if field_type == "code":
        # Compare the 16-digit numeric line after removing leading zeros.
        digits = re.sub(r'\D', '', word)
        if not digits: return False
        try:
            return str(int(digits)) == target
        except ValueError:
            return False
    return normalize_amount(word) == target


def find_bounding_box(img, target: str, field_type: str):
    """Return the target bounding box (x0, y0, x1, y1), or None."""
    if not target or target in ("0", "0,00", "N/A"):
        return None
    try:
        data = pytesseract.image_to_data(
            img, lang='eng', config=TESSERACT_CONFIG, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return None

    for k, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        if not word:
            continue
        if _matches(word, target, field_type):
            x, y = data["left"][k], data["top"][k]
            return (x, y, x + data["width"][k], y + data["height"][k])
    return None


def _scale_box(box, scale):
    return tuple(int(v * scale) for v in box)


def _pad_box(box, factor=0.45, minimum=4.0):
    """Pad the rectangle so its border does not obscure the text."""
    x0, y0, x1, y1 = box
    margin = max(minimum, (y1 - y0) * factor)
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def generate_annotated_image(img_gray, result, max_width=1400):
    """Highlight the extracted fields on the original page.

    Locate each field using its winning image strategy, then map the coordinates
    back to the original image so the preview remains readable."""
    highlights = []
    for field_type, strategy_key, color in (
        ("code", "code_strategy", "#2563eb"),
        ("amount", "amount_strategy", "#16a34a"),
    ):
        target = result.get(field_type)
        index = result.get(strategy_key)
        if not target or index is None:
            continue
        try:
            strategy_image = STRATEGIES[index][1](img_gray)
        except Exception:
            continue
        box = find_bounding_box(strategy_image, target, field_type)
        if not box:
            continue
        # Map the strategy coordinates back to the original image.
        factor = img_gray.width / strategy_image.width
        highlights.append((_pad_box(tuple(v * factor for v in box)), color, STRATEGIES[index][0]))

    scale = min(1.0, max_width / img_gray.width)
    preview = img_gray.convert("RGB")
    if scale < 1.0:
        preview = preview.resize((int(img_gray.width * scale), int(img_gray.height * scale)))

    drawing = ImageDraw.Draw(preview)
    thickness = max(2, preview.width // 500)
    for box, color, _name in highlights:
        drawing.rectangle(_scale_box(box, scale), outline=color, width=thickness)

    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=70, optimize=True)
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "code_found": any(c == "#2563eb" for _b, c, _n in highlights),
        "amount_found": any(c == "#16a34a" for _b, c, _n in highlights),
    }


# ==========================================
# Audit event stream
# ==========================================

REPORT_COLUMNS = [
    "Page", "Code (Reference PDF)", "Code (OCR Slips)", "Code Status",
    "Amount (Reference PDF)", "Amount (OCR Slips)", "Amount Status", "Overall Status",
    "Category",
]

# Discrepancy categories for human review.
CATEGORY_OTHER_REGISTRATION = "OTHER REGISTRATION"
CATEGORY_UNREAD = "UNREAD"
CATEGORY_REVIEW = "REVIEW"

# Sentinel readings representing missing OCR data.
_EMPTY_CODE_READINGS = {"", "0", None}
_EMPTY_AMOUNT_READINGS = {"", "0,00", None}


def classify_discrepancy(result, known_registrations):
    """Classify mismatches as another registration, unread data, or manual review.

    Categories prioritize inspection; they cannot distinguish OCR errors from
    genuine document discrepancies without human review."""
    if result["overall_status"] != "ERROR":
        return ""

    if result["code_status"] != "OK":
        code = result["code"]
        if code in _EMPTY_CODE_READINGS:
            return CATEGORY_UNREAD
        if code in known_registrations:
            return CATEGORY_OTHER_REGISTRATION
        return CATEGORY_REVIEW

    if result["amount"] in _EMPTY_AMOUNT_READINGS:
        return CATEGORY_UNREAD
    return CATEGORY_REVIEW


def _poppler_kwargs():
    # Without a configured directory, pdf2image searches PATH.
    return {"poppler_path": POPPLER_PATH} if (POPPLER_PATH and os.path.isdir(POPPLER_PATH)) else {}


def stream_audit(payment_slips_path, reference_path, dpi=DEFAULT_DPI, include_image=True):
    """Yield start, page, and end events while rendering one PDF page at a time."""
    reference_list = extract_reference_list(reference_path)
    known_registrations = {item["code"] for item in reference_list if item["code"] != "N/A"}

    info = pdf2image.pdfinfo_from_path(payment_slips_path, **_poppler_kwargs())
    total_pages = info["Pages"]

    yield {
        "type": "start",
        "total_pages": total_pages,
        "reference_count": len(reference_list),
        "dpi": dpi,
    }

    for number in range(1, total_pages + 1):
        print(f"Processing page {number}...")
        pages = pdf2image.convert_from_path(
            payment_slips_path, dpi=dpi, first_page=number, last_page=number, **_poppler_kwargs()
        )
        if not pages:
            continue

        index = number - 1
        expected_entry = reference_list[index] if (reference_list and index < len(reference_list)) else {"code": "N/A", "amount": "N/A"}

        img_gray = pages[0].convert('L')
        result = process_page_with_consensus(img_gray, expected_entry["code"], expected_entry["amount"])
        category = classify_discrepancy(result, known_registrations)

        if result["overall_status"] == "ERROR":
            print(f"   [MISMATCH/{category}] Page {number} | Expected: {expected_entry['code']} - {expected_entry['amount']} | Read: {result['code']} - {result['amount']}")

        row = {
            "Page": number,
            "Code (Reference PDF)": expected_entry["code"],
            "Code (OCR Slips)": result["code"],
            "Code Status": result["code_status"],
            "Amount (Reference PDF)": expected_entry["amount"],
            "Amount (OCR Slips)": result["amount"],
            "Amount Status": result["amount_status"],
            "Overall Status": result["overall_status"],
            "Category": category,
        }

        event = {"type": "page", "page": number, "total_pages": total_pages, "row": row}

        if include_image:
            strategy_index = result.get("code_strategy")
            if strategy_index is None:
                strategy_index = result.get("amount_strategy")
            event["strategy"] = STRATEGIES[strategy_index][0] if strategy_index is not None else None
            try:
                event.update(generate_annotated_image(img_gray, result))
            except Exception as e:
                event["image_error"] = str(e)

        yield event

    yield {"type": "end", "total_pages": total_pages, "model": "Tesseract OCR"}
