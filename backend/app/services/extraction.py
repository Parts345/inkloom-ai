
"""
Owner: Riyesh
Inkloom AI (Team Caraxes) - document and image extraction service.

Supported inputs:
    - Pasted text
    - PDF documents
    - DOCX documents
    - PNG, JPG, JPEG, TIFF, BMP, WEBP and GIF images

Project contract:
    extract_units(
        *,
        text: Optional[str],
        file_name: Optional[str],
        file_bytes: Optional[bytes]
    ) -> List[ContentUnit]

The output uses the existing ContentUnit and UnitMetadata models
from app.schemas. No changes to schemas.py or orchestrator.py are required.

Optional OCR:
    - pytesseract Python package
    - Tesseract OCR system executable
    - Pillow

Important:
    Raw image bytes are not stored in ContentUnit because the existing
    schema has no binary-data field. Image units contain a description,
    dimensions and available alt text instead.
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile

from collections import Counter
from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4

from app.schemas import ContentUnit


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


try:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
except ImportError:
    docx = None
    qn = None
    Table = None
    Paragraph = None


try:
    from PIL import Image
except ImportError:
    Image = None


try:
    import pytesseract
except ImportError:
    pytesseract = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OCR_LANG = os.getenv("OCR_LANG", "eng")
OCR_DPI = int(os.getenv("OCR_DPI", "300"))

MIN_TEXT_CHARS_PER_PAGE = int(
    os.getenv("MIN_TEXT_CHARS_PER_PAGE", "25")
)

MAX_PAGES = int(
    os.getenv("EXTRACT_MAX_PAGES", "200")
)

HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 150
MAX_HEADING_WORDS = 20

MIN_IMAGE_SIDE_PX = 10

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}


if (
    os.getenv("TESSERACT_CMD")
    and pytesseract is not None
):
    pytesseract.pytesseract.tesseract_cmd = os.environ[
        "TESSERACT_CMD"
    ]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsupportedFormatError(ValueError):
    """Raised when an uploaded file cannot be processed."""


# ---------------------------------------------------------------------------
# Unit creation
# ---------------------------------------------------------------------------

def _new_unit_id() -> str:
    """Generate a unique ID for each extracted content unit."""
    return f"u_{uuid4().hex}"


def _create_text_unit(
    content: str,
    page: Optional[int] = None,
    heading_level: Optional[int] = None,
) -> ContentUnit:
    """Create a text ContentUnit using the existing project schema."""

    metadata = {}

    if page is not None:
        metadata["page"] = page

    if heading_level is not None:
        metadata["heading_level"] = heading_level

    return ContentUnit(
        unit_id=_new_unit_id(),
        unit_type="text",
        content=content,
        metadata=metadata,
    )


def _create_image_unit(
    description: str,
    page: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    alt_text: Optional[str] = None,
) -> ContentUnit:
    """Create an image ContentUnit using the existing project schema."""

    metadata = {}

    if page is not None:
        metadata["page"] = page

    if alt_text:
        metadata["alt_text"] = alt_text

    if width is not None and height is not None:
        metadata["dimensions"] = {
            "width": int(width),
            "height": int(height),
        }

    return ContentUnit(
        unit_id=_new_unit_id(),
        unit_type="image",
        content=description,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Clean unnecessary spaces and null characters."""

    text = text.replace("\u00a0", " ")
    text = text.replace("\x00", "")

    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def _split_paragraphs(text: str) -> List[str]:
    """Split pasted text into separate paragraphs."""

    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]


def _require(module: Any, name: str) -> None:
    """Check whether a required dependency is installed."""

    if module is None:
        raise UnsupportedFormatError(
            f"Required library '{name}' is not installed."
        )


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

def _detect_type(
    file_bytes: bytes,
    file_name: Optional[str],
) -> str:
    """Detect the uploaded file type using its contents and extension."""

    if file_bytes.startswith(b"%PDF-"):
        return "pdf"

    if file_bytes.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:

                if "word/document.xml" in archive.namelist():
                    return "docx"

        except zipfile.BadZipFile:
            pass

        raise UnsupportedFormatError(
            "The uploaded ZIP-based file is not a valid DOCX document."
        )

    extension = (
        Path(file_name).suffix.lower()
        if file_name
        else ""
    )

    if extension == ".doc":
        raise UnsupportedFormatError(
            "Old .doc files are not supported. "
            "Save the document as .docx or PDF."
        )

    if extension in IMAGE_EXTENSIONS:
        return "image"

    if Image is not None:
        try:
            with Image.open(io.BytesIO(file_bytes)) as image:
                image.verify()

            return "image"

        except Exception:
            pass

    raise UnsupportedFormatError(
        "Unsupported file format. "
        "Upload a PDF, DOCX, PNG, JPG or another supported image."
    )


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------

_ocr_checked: Optional[bool] = None


def _ocr_available() -> bool:
    """Check whether both pytesseract and Tesseract are available."""

    global _ocr_checked

    if _ocr_checked is not None:
        return _ocr_checked

    if pytesseract is None or Image is None:
        _ocr_checked = False
        return False

    try:
        pytesseract.get_tesseract_version()
        _ocr_checked = True

    except Exception as exc:
        logger.warning(
            "Tesseract OCR is unavailable: %s",
            exc,
        )
        _ocr_checked = False

    return _ocr_checked


def _ocr_image(
    image: Any,
) -> str:
    """Extract text from a PIL image using Tesseract."""

    if not _ocr_available():
        return ""

    try:
        data = pytesseract.image_to_data(
            image,
            lang=OCR_LANG,
            output_type=pytesseract.Output.DICT,
        )

        lines = {}

        for index, word in enumerate(data["text"]):

            word = word.strip()

            if not word:
                continue

            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )

            lines.setdefault(key, []).append(word)

        extracted_text = "\n".join(
            " ".join(words)
            for _, words in sorted(lines.items())
        )

        return _clean(extracted_text)

    except Exception as exc:
        logger.warning("OCR failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _extract_pdf(
    file_bytes: bytes,
) -> List[ContentUnit]:
    """Extract PDF text, headings, images and scanned-page OCR."""

    _require(fitz, "pymupdf")

    try:
        document = fitz.open(
            stream=file_bytes,
            filetype="pdf",
        )

    except Exception as exc:
        raise UnsupportedFormatError(
            f"Could not open the PDF document: {exc}"
        ) from exc

    units: List[ContentUnit] = []

    with document:

        if document.needs_pass:
            raise UnsupportedFormatError(
                "Password-protected PDFs are not supported."
            )

        if document.page_count == 0:
            raise UnsupportedFormatError(
                "The PDF document contains no pages."
            )

        if document.page_count > MAX_PAGES:
            logger.warning(
                "PDF has %s pages. Only the first %s will be processed.",
                document.page_count,
                MAX_PAGES,
            )

        page_records = []
        font_sizes = Counter()

        # First pass: extract text and collect font sizes.
        for page_index in range(
            min(document.page_count, MAX_PAGES)
        ):

            page = document[page_index]

            page_number = page_index + 1

            try:
                blocks = page.get_text("dict").get(
                    "blocks",
                    [],
                )

            except Exception as exc:
                logger.warning(
                    "Could not extract text from PDF page %s: %s",
                    page_number,
                    exc,
                )

                blocks = []

            page_lines = []
            page_text_parts = []

            for block in blocks:

                if block.get("type") != 0:
                    continue

                for line in block.get("lines", []):

                    spans = line.get("spans", [])

                    line_text = _clean(
                        "".join(
                            span.get("text", "")
                            for span in spans
                        )
                    )

                    if not line_text:
                        continue

                    sizes = [
                        float(span.get("size", 0))
                        for span in spans
                        if _clean(span.get("text", ""))
                    ]

                    if not sizes:
                        continue

                    line_size = max(sizes)

                    font_sizes[round(line_size, 1)] += len(
                        line_text
                    )

                    page_lines.append(
                        {
                            "text": line_text,
                            "size": round(line_size, 1),
                        }
                    )

                    page_text_parts.append(line_text)

            page_text = "\n".join(page_text_parts)

            page_records.append(
                {
                    "page": page_number,
                    "lines": page_lines,
                    "text": page_text,
                    "page_object": page,
                }
            )

        # Determine the most common font size as body text size.
        body_size = None

        if font_sizes:
            body_size = font_sizes.most_common(1)[0][0]

        heading_sizes = []

        if body_size is not None:

            heading_sizes = sorted(
                {
                    line["size"]
                    for record in page_records
                    for line in record["lines"]
                    if line["size"]
                    >= body_size * HEADING_SIZE_RATIO
                    and len(line["text"]) <= MAX_HEADING_CHARS
                    and len(line["text"].split())
                    <= MAX_HEADING_WORDS
                },
                reverse=True,
            )[:6]

        heading_levels = {
            size: index + 1
            for index, size in enumerate(heading_sizes)
        }

        # Second pass: create ContentUnit objects.
        for record in page_records:

            page_number = record["page"]

            page_text = record["text"]

            page_object = record["page_object"]

            # OCR fallback for scanned or nearly empty pages.
            if (
                len(page_text) < MIN_TEXT_CHARS_PER_PAGE
                and _ocr_available()
            ):

                try:
                    pixmap = page_object.get_pixmap(
                        dpi=OCR_DPI,
                        alpha=False,
                    )

                    image = Image.frombytes(
                        "RGB",
                        (pixmap.width, pixmap.height),
                        pixmap.samples,
                    )

                    ocr_text = _ocr_image(image)

                    if ocr_text:
                        page_text = ocr_text

                        for paragraph in _split_paragraphs(
                            ocr_text
                        ):
                            units.append(
                                _create_text_unit(
                                    paragraph,
                                    page=page_number,
                                )
                            )

                except Exception as exc:
                    logger.warning(
                        "OCR failed on PDF page %s: %s",
                        page_number,
                        exc,
                    )

            else:

                # Separate detected headings from normal text.
                normal_lines = []

                for line in record["lines"]:

                    line_text = line["text"]

                    heading_level = heading_levels.get(
                        line["size"]
                    )

                    if heading_level is not None:

                        if normal_lines:
                            units.append(
                                _create_text_unit(
                                    "\n".join(normal_lines),
                                    page=page_number,
                                )
                            )

                            normal_lines = []

                        units.append(
                            _create_text_unit(
                                line_text,
                                page=page_number,
                                heading_level=heading_level,
                            )
                        )

                    else:
                        normal_lines.append(line_text)

                if normal_lines:
                    units.append(
                        _create_text_unit(
                            "\n".join(normal_lines),
                            page=page_number,
                        )
                    )

            # Extract embedded images from this page.
            seen_images = set()

            try:
                image_list = page_object.get_images(
                    full=True
                )

            except Exception as exc:
                logger.warning(
                    "Could not list images on PDF page %s: %s",
                    page_number,
                    exc,
                )

                image_list = []

            for image_index, image_info in enumerate(
                image_list,
                start=1,
            ):

                xref = image_info[0]

                if xref in seen_images:
                    continue

                seen_images.add(xref)

                try:
                    extracted = document.extract_image(xref)

                    if not extracted:
                        continue

                    width = extracted.get("width")
                    height = extracted.get("height")

                    if (
                        width is not None
                        and height is not None
                        and width < MIN_IMAGE_SIDE_PX
                        and height < MIN_IMAGE_SIDE_PX
                    ):
                        continue

                    image_format = (
                        extracted.get("ext") or "unknown"
                    ).upper()

                    description = (
                        f"Embedded PDF image {image_index} "
                        f"({image_format}, "
                        f"{width or 'unknown'} x "
                        f"{height or 'unknown'} pixels)."
                    )

                    units.append(
                        _create_image_unit(
                            description=description,
                            page=page_number,
                            width=width,
                            height=height,
                        )
                    )

                except Exception as exc:
                    logger.warning(
                        "Could not extract PDF image %s on page %s: %s",
                        image_index,
                        page_number,
                        exc,
                    )

    return units


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

_HEADING_STYLE = re.compile(
    r"^heading\s*([1-6])$",
    re.IGNORECASE,
)


def _docx_heading_level(
    paragraph: Any,
) -> Optional[int]:
    """Read heading levels from actual Word paragraph styles."""

    try:
        style_name = (
            paragraph.style.name
            if paragraph.style is not None
            else ""
        )

    except Exception:
        return None

    style_name = (style_name or "").strip()

    if style_name.lower() == "title":
        return 1

    match = _HEADING_STYLE.match(style_name)

    if match:
        return int(match.group(1))

    return None


def _extract_docx_images(
    document: Any,
    units: List[ContentUnit],
) -> None:
    """Extract DOCX image descriptions, dimensions and alt text."""

    try:
        drawings = document.element.body.xpath(
            ".//w:drawing"
        )

    except Exception as exc:
        logger.warning(
            "Could not inspect DOCX images: %s",
            exc,
        )
        return

    image_index = 0

    for drawing in drawings:

        try:
            doc_properties = drawing.xpath(
                ".//wp:docPr"
            )

            image_blips = drawing.xpath(
                ".//a:blip"
            )

            if not image_blips:
                continue

            image_index += 1

            alt_text = None

            if doc_properties:

                alt_text = (
                    doc_properties[0].get("descr")
                    or doc_properties[0].get("title")
                    or None
                )

                if alt_text:
                    alt_text = alt_text.strip() or None

            relationship_id = image_blips[0].get(
                qn("r:embed")
            )

            image_blob = None

            if (
                relationship_id
                and relationship_id in document.part.related_parts
            ):
                image_blob = document.part.related_parts[
                    relationship_id
                ].blob

            width = None
            height = None
            image_format = "unknown"

            if image_blob and Image is not None:

                try:
                    with Image.open(
                        io.BytesIO(image_blob)
                    ) as image:

                        width = image.width
                        height = image.height

                        image_format = (
                            image.format or "unknown"
                        ).upper()

                except Exception:
                    pass

            description = (
                f"Embedded DOCX image {image_index} "
                f"({image_format}, "
                f"{width or 'unknown'} x "
                f"{height or 'unknown'} pixels)."
            )

            units.append(
                _create_image_unit(
                    description=description,
                    page=1,
                    width=width,
                    height=height,
                    alt_text=alt_text,
                )
            )

        except Exception as exc:
            logger.warning(
                "Could not process DOCX image %s: %s",
                image_index,
                exc,
            )


def _extract_docx(
    file_bytes: bytes,
) -> List[ContentUnit]:
    """Extract DOCX paragraphs, headings, tables and images."""

    _require(docx, "python-docx")

    try:
        document = docx.Document(
            io.BytesIO(file_bytes)
        )

    except Exception as exc:
        raise UnsupportedFormatError(
            f"Could not open the DOCX document: {exc}"
        ) from exc

    units: List[ContentUnit] = []

    try:
        blocks = list(
            document.iter_inner_content()
        )

    except AttributeError:
        # Compatibility with older python-docx versions.
        blocks = list(document.paragraphs)

    for block in blocks:

        # Extract table rows.
        if Table is not None and isinstance(block, Table):

            for row in block.rows:

                cells = [
                    _clean(cell.text)
                    for cell in row.cells
                ]

                row_text = " | ".join(
                    cell for cell in cells if cell
                )

                if row_text:
                    units.append(
                        _create_text_unit(
                            row_text,
                            page=1,
                        )
                    )

            continue

        # Extract paragraphs.
        if Paragraph is not None and isinstance(
            block,
            Paragraph,
        ):

            paragraph_text = _clean(block.text)

            if not paragraph_text:
                continue

            heading_level = _docx_heading_level(
                block
            )

            units.append(
                _create_text_unit(
                    paragraph_text,
                    page=1,
                    heading_level=heading_level,
                )
            )

    # DOCX does not have fixed page numbers in its document structure.
    # The existing schema uses page=1 for this extraction.
    _extract_docx_images(
        document,
        units,
    )

    return units


# ---------------------------------------------------------------------------
# Standalone image extraction
# ---------------------------------------------------------------------------

def _extract_image(
    file_bytes: bytes,
) -> List[ContentUnit]:
    """Extract image dimensions, create an image unit and run OCR."""

    _require(Image, "Pillow")

    try:
        image = Image.open(
            io.BytesIO(file_bytes)
        )

        image.load()

    except Exception as exc:
        raise UnsupportedFormatError(
            f"Could not open the image: {exc}"
        ) from exc

    width = image.width
    height = image.height

    image_format = (
        image.format or "unknown"
    ).upper()

    description = (
        f"Uploaded image ({image_format}, "
        f"{width} x {height} pixels)."
    )

    units = [
        _create_image_unit(
            description=description,
            page=1,
            width=width,
            height=height,
        )
    ]

    # OCR text is returned as a separate text unit.
    if _ocr_available():

        extracted_text = _ocr_image(
            image.convert("RGB")
        )

        if extracted_text:

            for paragraph in _split_paragraphs(
                extracted_text
            ):

                units.append(
                    _create_text_unit(
                        paragraph,
                        page=1,
                    )
                )

    return units


# ---------------------------------------------------------------------------
# Public API - used by orchestrator.py
# ---------------------------------------------------------------------------

def extract_units(
    *,
    text: Optional[str],
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> List[ContentUnit]:
    """
    Extract content from pasted text or an uploaded file.

    This function intentionally preserves the exact interface
    expected by backend/app/services/orchestrator.py.

    Exactly one input source should be provided:
        - text
        - file_bytes

    Returns:
        List[ContentUnit]

    Raises:
        UnsupportedFormatError:
            If the uploaded file is unsupported, corrupt or cannot
            be processed due to a required missing dependency.
    """

    # Pasted text extraction.
    if text is not None:

        paragraphs = _split_paragraphs(text)

        return [
            _create_text_unit(paragraph)
            for paragraph in paragraphs
        ]

    # File extraction.
    if file_bytes is not None:

        if not file_bytes:
            raise UnsupportedFormatError(
                "The uploaded file is empty."
            )

        file_type = _detect_type(
            file_bytes,
            file_name,
        )

        logger.info(
            "Extracting file '%s' as %s (%s bytes).",
            file_name or "unnamed",
            file_type,
            len(file_bytes),
        )

        if file_type == "pdf":
            return _extract_pdf(file_bytes)

        if file_type == "docx":
            return _extract_docx(file_bytes)

        if file_type == "image":
            return _extract_image(file_bytes)

    return []
