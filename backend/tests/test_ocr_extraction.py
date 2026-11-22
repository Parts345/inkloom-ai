
"""
Unit tests for digital, scanned and hybrid PDF extraction.

These tests use the existing extract_units() function.
OCR is mocked so the tests do not require Tesseract or RapidOCR
to be installed just to verify the extraction pipeline.
"""

import io

import pytest
import pymupdf as fitz
from PIL import Image, ImageDraw

from app.services import extraction


# ---------------------------------------------------------
# Helper: Create a digital PDF
# ---------------------------------------------------------

def create_digital_pdf():
    document = fitz.open()

    page = document.new_page()

    page.insert_textbox(
        fitz.Rect(50, 50, 550, 200),
        "This is a digital PDF document. "
        "Its text should be extracted directly without OCR.",
        fontsize=12,
    )

    pdf_bytes = document.tobytes()
    document.close()

    return pdf_bytes


# ---------------------------------------------------------
# Helper: Create a scanned PDF
# ---------------------------------------------------------

def create_scanned_pdf():
    image = Image.new("RGB", (1200, 400), "white")

    draw = ImageDraw.Draw(image)

    draw.text(
        (50, 100),
        "This is a scanned PDF document.",
        fill="black",
    )

    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")

    document = fitz.open()
    page = document.new_page()

    page.insert_image(
        page.rect,
        stream=image_buffer.getvalue(),
    )

    pdf_bytes = document.tobytes()
    document.close()

    return pdf_bytes


# ---------------------------------------------------------
# Helper: Create a hybrid PDF
# ---------------------------------------------------------

def create_hybrid_pdf():
    document = fitz.open()

    # Page 1: Digital text
    digital_page = document.new_page()

    digital_page.insert_textbox(
        fitz.Rect(50, 50, 550, 200),
        "This is the digital page of a hybrid PDF. "
        "It should be extracted without OCR.",
        fontsize=12,
    )

    # Page 2: Scanned image
    image = Image.new("RGB", (1200, 400), "white")

    draw = ImageDraw.Draw(image)

    draw.text(
        (50, 100),
        "This is the scanned page.",
        fill="black",
    )

    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")

    scanned_page = document.new_page()

    scanned_page.insert_image(
        scanned_page.rect,
        stream=image_buffer.getvalue(),
    )

    pdf_bytes = document.tobytes()
    document.close()

    return pdf_bytes


# ---------------------------------------------------------
# Helper: Get extracted text
# ---------------------------------------------------------

def get_extracted_text(units):
    return "\n".join(
        unit.content
        for unit in units
        if unit.unit_type == "text"
    )


# ---------------------------------------------------------
# Test 1: Digital PDF extraction
# ---------------------------------------------------------

def test_digital_pdf_extraction(monkeypatch):

    # Make OCR available, but ensure it is not called.
    monkeypatch.setattr(
        extraction,
        "_ocr_available",
        lambda: True,
    )

    def unexpected_ocr(image):
        pytest.fail("OCR should not run for a digital PDF.")

    monkeypatch.setattr(
        extraction,
        "_ocr_image",
        unexpected_ocr,
    )

    pdf_bytes = create_digital_pdf()

    units = extraction.extract_units(
        text=None,
        file_name="digital.pdf",
        file_bytes=pdf_bytes,
    )

    extracted_text = get_extracted_text(units)

    assert units
    assert "digital PDF document" in extracted_text
    assert "without OCR" in extracted_text

    assert all(unit.metadata.page == 1 for unit in units)


# ---------------------------------------------------------
# Test 2: Scanned PDF extraction
# ---------------------------------------------------------

def test_scanned_pdf_extraction(monkeypatch):

    expected_ocr_text = (
        "This text was extracted from a scanned PDF using OCR."
    )

    monkeypatch.setattr(
        extraction,
        "_ocr_available",
        lambda: True,
    )

    monkeypatch.setattr(
        extraction,
        "_ocr_image",
        lambda image: expected_ocr_text,
    )

    pdf_bytes = create_scanned_pdf()

    units = extraction.extract_units(
        text=None,
        file_name="scanned.pdf",
        file_bytes=pdf_bytes,
    )

    extracted_text = get_extracted_text(units)

    assert units
    assert expected_ocr_text in extracted_text

    assert any(
        unit.metadata.page == 1
        for unit in units
        if unit.unit_type == "text"
    )


# ---------------------------------------------------------
# Test 3: Hybrid PDF extraction
# ---------------------------------------------------------

def test_hybrid_pdf_extraction(monkeypatch):

    expected_ocr_text = (
        "This text was extracted from the scanned page using OCR."
    )

    monkeypatch.setattr(
        extraction,
        "_ocr_available",
        lambda: True,
    )

    monkeypatch.setattr(
        extraction,
        "_ocr_image",
        lambda image: expected_ocr_text,
    )

    pdf_bytes = create_hybrid_pdf()

    units = extraction.extract_units(
        text=None,
        file_name="hybrid.pdf",
        file_bytes=pdf_bytes,
    )

    extracted_text = get_extracted_text(units)

    # Check digital page extraction.
    assert "digital page of a hybrid PDF" in extracted_text

    # Check scanned page OCR extraction.
    assert expected_ocr_text in extracted_text

    # Check page numbers.
    text_units = [
        unit for unit in units
        if unit.unit_type == "text"
    ]

    assert any(
        unit.metadata.page == 1
        for unit in text_units
    )

    assert any(
        unit.metadata.page == 2
        for unit in text_units
    )
