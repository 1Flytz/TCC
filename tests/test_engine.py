"""Test exact registration comparison, amount normalization, and reference parsing.

Known parser limitations are recorded explicitly to make behavior changes visible."""

import pytest

from core import engine

# ==========================================
# code_status
# ==========================================


@pytest.mark.parametrize("read, expected", [
    ("116530", "116530"),
    ("113640", "113640"),
    ("59750", "59750"),      # five-digit suffix
])
def test_identical_code_matches(read, expected):
    assert engine.code_status(read, expected) == "OK"


@pytest.mark.parametrize("read, expected", [
    ("113640", "113610"),   # 4 -> 1
    ("61990", "61890"),     # 9 -> 8
    ("116530", "116230"),   # 5 -> 2
    ("60700", "60900"),     # 7 -> 9
])
def test_unrelated_digit_changes_are_flagged(read, expected):
    assert engine.code_status(read, expected) == "MISMATCH"


@pytest.mark.parametrize("read, expected, reason", [
    ("0", "116530", "OCR could not read the slip"),
    ("", "116530", "empty reading"),
    ("116530", "N/A", "reference list ended before the slips"),
    ("116530", "", "empty expected value"),
])
def test_invalid_pairs_do_not_match(read, expected, reason):
    assert engine.code_status(read, expected) == "MISMATCH", reason


def test_codes_without_trailing_zero_do_not_match():
    """Codes without trailing zero do not match."""
    assert engine.code_status("12345", "12345") == "MISMATCH"


@pytest.mark.parametrize("read, expected, confusion", [
    ("116110", "116170", "1 read as 7"),
    ("113640", "113840", "6 read as 8"),
    ("119000", "119090", "0 read as 9"),
    ("116110", "116770", "two 1s read as 7"),
])
def test_ocr_digit_confusions_are_flagged(read, expected, confusion):
    """Ocr digit confusions are flagged."""
    assert engine.code_status(read, expected) == "MISMATCH", confusion


def test_distinct_registrations_never_match():
    """Distinct registrations never match."""
    from itertools import combinations

    codes = [
        "113640", "113840", "116110", "116170", "116770", "116250", "118250",
        "116700", "118700", "116810", "118870", "119000", "119090", "118680",
        "118860", "118880", "117510", "117570",
    ]
    collisions = [
        (a, b) for a, b in combinations(codes, 2)
        if engine.code_status(a, b) == "OK"
    ]
    assert collisions == []


# ==========================================
# classify_discrepancy
# ==========================================

REGISTRATIONS = {"115870", "118870", "116110", "116170"}


def _result(code="115870", amount="76,82", code_status="OK", amount_status="OK", overall_status="ERROR"):
    return {
        "code": code,
        "amount": amount,
        "code_status": code_status,
        "amount_status": amount_status,
        "overall_status": overall_status,
    }


def test_matching_page_has_no_category():
    result = _result(overall_status="OK")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == ""


def test_another_registration_is_prioritized():
    """Another registration is prioritized."""
    result = _result(code="118870", code_status="MISMATCH")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_OTHER_REGISTRATION


@pytest.mark.parametrize("code", ["0", "", None])
def test_missing_code_is_unread(code):
    result = _result(code=code, code_status="MISMATCH")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_UNREAD


def test_unknown_code_requires_review():
    """Unknown code requires review."""
    result = _result(code="716110", code_status="MISMATCH")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_REVIEW


def test_missing_amount_with_matching_code_is_unread():
    result = _result(amount="0,00", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_UNREAD


def test_amount_mismatch_requires_review():
    result = _result(amount="99,99", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_REVIEW


def test_other_registration_takes_priority_over_unread_amount():
    """Other registration takes priority over unread amount."""
    result = _result(
        code="118870", amount="0,00", code_status="MISMATCH", amount_status="MISMATCH"
    )
    assert engine.classify_discrepancy(result, REGISTRATIONS) == engine.CATEGORY_OTHER_REGISTRATION


# ==========================================
# normalize_amount
# ==========================================


@pytest.mark.parametrize("text, expected", [
    ("82,30", "82,30"),
    ("76.82", "76,82"),                 # period instead of comma
    ("Total due 82,30 by 21/11", "82,30"),  # extract from a longer line
    ("82‚30", "82,30"),            # low comma
    ("82’30", "82,30"),            # curly apostrophe
    ("82`30", "82,30"),                 # backtick
    ("82´30", "82,30"),            # acute accent
    ("999,99", "999,99"),               # accepted upper bound
])
def test_amount_is_normalized_to_report_format(text, expected):
    assert engine.normalize_amount(text) == expected


@pytest.mark.parametrize("text", [
    "1000,00",   # above the 999,99 limit
    "82,3",      # only one decimal place
    "abc",
    "",
    None,
])
def test_invalid_amount_returns_none(text):
    assert engine.normalize_amount(text) is None


def test_amount_below_ten_reais_is_not_recognized():
    """Known limitation: the regex requires two or three integer digits.

    Amounts below 10,00 are not recognized; keep this limitation explicit."""
    assert engine.normalize_amount("5,30") is None


# ==========================================
# extract_reference_list
# ==========================================


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, pages):
        self.pages = [_FakePage(text) for text in pages]


@pytest.fixture
def reference(monkeypatch):
    """Replace the PDF reader with fixed text to test parsing without private PDFs."""
    def _read(*pages):
        monkeypatch.setattr(engine.pypdf, "PdfReader", lambda _path: _FakeReader(pages))
        return engine.extract_reference_list("sample_reference.pdf")
    return _read


def test_reference_list_restores_trailing_zero(reference):
    """Reference list restores trailing zero."""
    assert reference("11364-0 76,82\n5975-0 82,30") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "59750", "amount": "82,30"},
    ]


def test_code_without_amount_gets_na(reference):
    assert reference("11364-0 5975-0 76,82") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "59750", "amount": "N/A"},
    ]


def test_amount_without_code_gets_na(reference):
    assert reference("11364-0 76,82 82,30") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "N/A", "amount": "82,30"},
    ]


def test_page_without_text_is_skipped(reference):
    assert reference("", "11364-0 76,82") == [{"code": "113640", "amount": "76,82"}]


def test_unreadable_pdf_returns_empty_list(monkeypatch):
    """Unreadable pdf returns empty list."""
    def raise_read_error(_path):
        raise OSError("Corrupt PDF")

    monkeypatch.setattr(engine.pypdf, "PdfReader", raise_read_error)
    assert engine.extract_reference_list("sample.pdf") == []


def test_extra_amount_shifts_positional_pairing(reference):
    """Known limitation: reference fields are paired by position, not by line.

    An extra total shifts the code/amount pairing and creates an orphan amount."""
    assert reference("Total 99,99\n11364-0 76,82") == [
        {"code": "113640", "amount": "99,99"},
        {"code": "N/A", "amount": "76,82"},
    ]
