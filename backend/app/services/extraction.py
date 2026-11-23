"""
Inkloom AI - Document and Image Extraction Service.

Supported inputs:
    - Pasted text
    - PDF documents (digital, scanned and hybrid)
    - DOCX documents
    - PNG, JPG, JPEG, TIFF, BMP, WEBP and GIF images

Main project contract:
    extract_units(
        *,
        text: Optional[str],
        file_name: Optional[str],
        file_bytes: Optional[bytes]
    ) -> List[ContentUnit]

OCR:
    1. RapidOCR (preferred)
    2. Tesseract (fallback)

PDF extraction:
    - Processes each page independently.
    - Detects pages with little or no selectable text.
    - Renders scanned pages at high DPI.
    - Extracts OCR text in reading order.
    - Supports mixed digital and scanned pages.
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile

from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any, List, Optional, Tuple
from uuid import uuid4

from app.schemas import ContentUnit


logger = logging.getLogger(__name__)


# ============================================================
# OPTIONAL DEPENDENCIES
# ============================================================

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
    from PIL import Image, ImageFilter, ImageOps
except ImportError:
    Image = None
    ImageFilter = None
    ImageOps = None


try:
    import pytesseract
except ImportError:
    pytesseract = None


try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None


try:
    import numpy as np
except ImportError:
    np = None


# ============================================================
# CONFIGURATION
# ============================================================

OCR_LANG = os.getenv("OCR_LANG", "eng")

# Resolution used when rendering scanned PDF pages.
OCR_DPI = int(os.getenv("OCR_DPI", "200"))

# Pages with fewer characters are considered scanned candidates.
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


if os.getenv("TESSERACT_CMD") and pytesseract is not None:
    pytesseract.pytesseract.tesseract_cmd = os.environ[
        "TESSERACT_CMD"
    ]


# ============================================================
# EXCEPTIONS
# ============================================================

class UnsupportedFormatError(ValueError):
    """Raised when an uploaded file cannot be processed."""


# ============================================================
# CONTENT UNIT CREATION
# ============================================================

def _new_unit_id() -> str:
    """Generate a unique ID for every content unit."""
    return f"u_{uuid4().hex}"


def _create_text_unit(
    content: str,
    page: Optional[int] = None,
    heading_level: Optional[int] = None,
    extraction_method: Optional[str] = None,
) -> ContentUnit:
    """Create a text ContentUnit using the existing project schema."""

    metadata = {}

    if page is not None:
        metadata["page"] = page

    if heading_level is not None:
        metadata["heading_level"] = heading_level

    if extraction_method is not None:
        metadata["extraction_method"] = extraction_method
        if extraction_method == "ocr":
            metadata["is_ocr"] = True

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
    """Create an image ContentUnit."""

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


# ============================================================
# GENERAL HELPERS
# ============================================================

def _clean(text: str) -> str:
    """Remove unnecessary spaces and null characters."""

    text = text.replace("\u00a0", " ")
    text = text.replace("\x00", "")

    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def _is_single_character_line(text: str) -> bool:
    return len(_clean(text)) == 1


def _drop_single_character_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not _is_single_character_line(line)
    )


def _split_paragraphs(text: str) -> List[str]:
    """Split text into separate paragraphs."""

    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", _drop_single_character_lines(text))
        if paragraph.strip()
    ]


def _require(module: Any, name: str) -> None:
    """Check whether a required dependency is installed."""

    if module is None:
        raise UnsupportedFormatError(
            f"Required library '{name}' is not installed."
        )


# ============================================================
# FILE TYPE DETECTION
# ============================================================

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


# ============================================================
# OCR ENGINE MANAGEMENT
# ============================================================

_rapidocr_engine: Any = None
_rapidocr_checked = False
_rapidocr_lock = Lock()

_tesseract_checked: Optional[bool] = None


def _get_rapidocr_engine() -> Any:
    """Initialize RapidOCR once, when it is first needed."""

    global _rapidocr_engine, _rapidocr_checked

    if _rapidocr_checked:
        return _rapidocr_engine

    with _rapidocr_lock:

        if _rapidocr_checked:
            return _rapidocr_engine

        _rapidocr_checked = True

        if RapidOCR is None:
            return None

        try:
            _rapidocr_engine = RapidOCR()

            logger.info(
                "RapidOCR engine initialized successfully."
            )

        except Exception as exc:
            _rapidocr_engine = None

            logger.warning(
                "RapidOCR initialization failed: %s",
                exc,
            )

    return _rapidocr_engine


def _tesseract_available() -> bool:
    """Check whether Tesseract and its executable are available."""

    global _tesseract_checked

    if _tesseract_checked is not None:
        return _tesseract_checked

    if pytesseract is None or Image is None:
        _tesseract_checked = False
        return False

    try:
        pytesseract.get_tesseract_version()

        _tesseract_checked = True

    except Exception as exc:
        logger.warning(
            "Tesseract OCR is unavailable: %s",
            exc,
        )

        _tesseract_checked = False

    return _tesseract_checked


def _ocr_available() -> bool:
    """Check whether at least one OCR engine is available."""

    if Image is None:
        return False

    if _get_rapidocr_engine() is not None:
        return True

    return _tesseract_available()


# ============================================================
# RAPIDOCR
# ============================================================

def _rapidocr_text(image: Any) -> str:
    """
    Extract text using RapidOCR.

    Results are sorted in reading order and grouped into lines by vertical spacing.
    """

    engine = _get_rapidocr_engine()

    if engine is None or np is None:
        logger.info("OCR engine=rapidocr lines=0")
        return ""

    try:
        rgb_image = image.convert("RGB")
        image_array = np.asarray(rgb_image)

        result = engine(image_array)

        # rapidocr-onnxruntime returns:
        # (detections, elapsed_time)
        detections = result[0] if isinstance(result, tuple) else result

        if not detections:
            logger.info("OCR engine=rapidocr lines=0")
            return ""

        ordered: List[Tuple[float, float, float, str]] = []

        for detection in detections:
            if not detection or len(detection) < 3:
                continue

            box, raw_text, raw_score = detection[:3]
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if score < 0.5:
                continue

            recognized_text = _clean(str(raw_text or ""))

            if not recognized_text:
                continue

            try:
                points = np.asarray(
                    box,
                    dtype=float
                ).reshape(-1, 2)

                top = float(points[:, 1].min())
                left = float(points[:, 0].min())
                height = float(points[:, 1].max() - top)

            except Exception:
                continue

            ordered.append(
                (top, left, height, recognized_text)
            )

        ordered.sort(
            key=lambda item: (item[0], item[1])
        )

        lines: List[str] = []
        previous_y: Optional[float] = None
        previous_height = 0.0
        for top, _, height, recognized_text in ordered:
            if lines:
                line_threshold = (previous_height + height) / 4
                if top - previous_y > line_threshold:
                    lines.append(recognized_text)
                else:
                    lines[-1] += " " + recognized_text
            else:
                lines.append(recognized_text)
            previous_y = top
            previous_height = height

        logger.info("OCR engine=rapidocr lines=%s", len(lines))
        lines = [line for line in lines if not _is_single_character_line(line)]
        return _clean("\n".join(lines))

    except Exception as exc:

        logger.warning(
            "RapidOCR failed; trying Tesseract fallback: %s",
            exc,
        )
        logger.info("OCR engine=rapidocr lines=0")

        return ""


# ============================================================
# TESSERACT FALLBACK
# ============================================================

def _tesseract_text(image: Any, psm: Optional[int] = None) -> str:
    """Extract text using Tesseract in visual reading order."""

    if not _tesseract_available():
        return ""

    try:
        options = {"config": f"--psm {psm}"} if psm is not None else {}
        data = pytesseract.image_to_data(
            image.convert("RGB"),
            lang=OCR_LANG,
            output_type=pytesseract.Output.DICT,
            **options,
        )

        lines = {}

        for index, raw_word in enumerate(
            data.get("text", [])
        ):

            word = _clean(str(raw_word or ""))

            if not word:
                continue

            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )

            top = int(data["top"][index])
            left = int(data["left"][index])

            lines.setdefault(key, []).append(
                (top, left, word)
            )

        ordered_lines = []

        for words in lines.values():

            words.sort(
                key=lambda item: item[1]
            )

            ordered_lines.append(
                (
                    min(item[0] for item in words),
                    min(item[1] for item in words),
                    " ".join(item[2] for item in words),
                )
            )

        ordered_lines.sort(
            key=lambda item: (item[0], item[1])
        )

        return _clean(
            _drop_single_character_lines("\n".join(item[2] for item in ordered_lines))
        )

    except Exception as exc:

        logger.warning(
            "Tesseract OCR failed: %s",
            exc,
        )

        return ""


# ============================================================
# OCR ROUTER
# ============================================================

def _preprocess_ocr_image(image: Any) -> Any:
    """Normalize image text for OCR without enlarging its long side past 3000px."""
    if Image is None or ImageFilter is None or ImageOps is None:
        return image

    processed = image.convert("L")
    width, height = processed.size
    shorter_side = min(width, height)
    longer_side = max(width, height)

    if shorter_side < 1500:
        scale = min(1500 / shorter_side, 3000 / longer_side)
        if scale > 1:
            processed = processed.resize(
                (round(width * scale), round(height * scale)),
                Image.Resampling.LANCZOS,
            )

    return ImageOps.autocontrast(processed).filter(ImageFilter.SHARPEN)


def _ocr_image(image: Any) -> str:
    """
    Use RapidOCR first and fall back to Tesseract only when it finds no text.
    """
    processed_image = _preprocess_ocr_image(image)
    rapid_text = _rapidocr_text(processed_image)
    if rapid_text:
        logger.info("OCR engine=rapidocr selected")
        return rapid_text

    logger.info("OCR engine=tesseract selected")
    fallback_text = _tesseract_text(processed_image)
    return fallback_text


_DEFAULT_OCR_IMAGE = _ocr_image


# ============================================================
# PDF EXTRACTION
# ============================================================

OCR_HEADING_RATIO = 1.3
MAX_EMBEDDED_IMAGES = int(os.getenv("MAX_EMBEDDED_IMAGES", "20"))


def _rapidocr_entries(image: Any) -> Optional[List[Tuple[float, float, float, str]]]:
    """Return (top, left, height, text) for every RapidOCR detection with score >= 0.5.

    Returns None when RapidOCR is unavailable, so the caller can use Tesseract. Returns []
    when RapidOCR ran and found no text.
    """
    engine = _get_rapidocr_engine()
    if engine is None or np is None:
        return None

    try:
        result = engine(np.asarray(image.convert("RGB")))
        detections = result[0] if isinstance(result, tuple) else result
        if not detections:
            return []

        measured_entries: List[Tuple[float, float, float, float, str]] = []
        for detection in detections:
            if not detection or len(detection) < 3:
                continue
            box, raw_text, raw_score = detection[:3]
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if score < 0.5:
                continue
            text = _clean(str(raw_text or ""))
            if not text:
                continue
            try:
                points = np.asarray(box, dtype=float).reshape(-1, 2)
                top = float(points[:, 1].min())
                left = float(points[:, 0].min())
                height = float(points[:, 1].max() - top)
                width = float(points[:, 0].max() - left)
            except Exception:
                continue

            dropped = bool(len(text) == 1 and text.isalpha() and width < 1.5 * height)
            logger.info(
                "OCR box text=%r score=%.2f width=%.0f height=%.0f dropped=%s",
                text,
                score,
                width,
                height,
                dropped,
            )
            if dropped:
                logger.debug(
                    "Dropping narrow single letter logo text=%r width=%.0f height=%.0f",
                    text,
                    width,
                    height,
                )
                continue

            measured_entries.append((top, left, height, width, text))

        ordered_entries = sorted(measured_entries, key=lambda entry: (entry[0], entry[1]))
        lines: List[List[Tuple[float, float, float, float, str]]] = []
        for entry in ordered_entries:
            top, _left, height, _width, _text = entry
            previous = lines[-1][-1] if lines else None
            if previous and (top - previous[0]) <= (previous[2] + height) / 4:
                lines[-1].append(entry)
            else:
                lines.append([entry])

        retained: List[Tuple[float, float, float, str]] = []
        for line in lines:
            for top, left, height, width, text in line:
                if len(line) == 1 and _is_single_character_line(text):
                    continue
                retained.append((top, left, height, text))
        return retained

    except Exception as exc:
        logger.warning("RapidOCR layout pass failed: %s", exc)
        return None


def _group_ocr_entries(
    entries: List[Tuple[float, float, float, str]],
) -> List[Tuple[str, bool]]:
    """Turn OCR boxes into (text, is_heading) blocks using line height and vertical gaps.

    Entries are read top to bottom, left to right, and merged into lines using the same spacing
    rule as _rapidocr_text. A line taller than OCR_HEADING_RATIO times the median line height,
    and short enough to be a title, becomes its own heading block. Other lines form paragraphs,
    which break where the vertical gap exceeds one median line height.
    """
    if not entries:
        return []

    ordered = sorted(entries, key=lambda e: (e[0], e[1]))
    heights = sorted(e[2] for e in ordered if e[2] > 0)
    median_height = heights[len(heights) // 2] if heights else 0.0

    lines: List[dict] = []
    for top, _left, height, text in ordered:
        if lines and (top - lines[-1]["top"]) <= (lines[-1]["height"] + height) / 4:
            lines[-1]["text"] += " " + text
            lines[-1]["height"] = max(lines[-1]["height"], height)
        else:
            lines.append({"top": top, "height": height, "text": text})

    blocks: List[Tuple[str, bool]] = []
    paragraph: List[str] = []
    previous_bottom: Optional[float] = None

    def flush() -> None:
        if paragraph:
            blocks.append((" ".join(paragraph), False))
            paragraph.clear()

    for line in lines:
        bottom = line["top"] + line["height"]
        is_heading = (
            median_height > 0
            and line["height"] >= median_height * OCR_HEADING_RATIO
            and len(line["text"]) <= MAX_HEADING_CHARS
            and len(line["text"].split()) <= MAX_HEADING_WORDS
        )
        if is_heading:
            flush()
            blocks.append((line["text"], True))
        else:
            if (
                previous_bottom is not None
                and median_height > 0
                and (line["top"] - previous_bottom) > median_height
            ):
                flush()
            paragraph.append(line["text"])
        previous_bottom = bottom

    flush()
    return blocks


def _ocr_page_blocks(image: Any) -> List[Tuple[str, bool]]:
    """OCR one rendered scanned page into (text, is_heading) blocks.

    RapidOCR gives box heights, so layout (headings and paragraph breaks) is available. When it
    finds nothing, Tesseract is used and its text is split into paragraphs without headings.
    """
    if globals().get("_ocr_image") is not _DEFAULT_OCR_IMAGE:
        fallback_text = _ocr_image(image)
        return [(paragraph, False) for paragraph in _split_paragraphs(fallback_text)]

    processed = _preprocess_ocr_image(image)

    entries = _rapidocr_entries(processed)
    if entries:
        logger.info("OCR engine=rapidocr layout entries=%s", len(entries))
        return _group_ocr_entries(entries)

    fallback_text = _tesseract_text(processed)
    if not fallback_text:
        return []
    logger.info("OCR engine=tesseract layout fallback (no heading detection)")
    return [(paragraph, False) for paragraph in _split_paragraphs(fallback_text)]


def extract_embedded_images(
    file_bytes: bytes,
    file_name: Optional[str],
) -> List[Tuple[bytes, str, Optional[int]]]:
    """Return (image_bytes, file_name, page) for pictures embedded in a PDF or DOCX.

    These images were not uploaded on their own, but they are part of the document, so the
    vision check reviews them too. Icons and tiny images are skipped, and at most
    MAX_EMBEDDED_IMAGES are returned.
    """
    suffix = Path(file_name).suffix.lower() if file_name else ""
    results: List[Tuple[bytes, str, Optional[int]]] = []

    if suffix == ".pdf":
        if fitz is None:
            return []
        try:
            document = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception:
            return []

        with document:
            seen_xrefs: set = set()
            for page_index in range(min(document.page_count, MAX_PAGES)):
                page = document[page_index]
                for image_index, image_ref in enumerate(page.get_images(full=True), start=1):
                    if len(results) >= MAX_EMBEDDED_IMAGES:
                        return results
                    xref = image_ref[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        info = document.extract_image(xref)
                    except Exception:
                        continue
                    if (
                        int(info.get("width", 0)) < MIN_IMAGE_SIDE_PX
                        or int(info.get("height", 0)) < MIN_IMAGE_SIDE_PX
                    ):
                        continue
                    image_ext = (info.get("ext") or "png").lower()
                    results.append(
                        (info["image"], f"page{page_index + 1}-image{image_index}.{image_ext}", page_index + 1)
                    )
        return results

    if suffix == ".docx":
        try:
            archive = zipfile.ZipFile(io.BytesIO(file_bytes))
        except zipfile.BadZipFile:
            return []
        with archive:
            for member in sorted(archive.namelist()):
                if not member.startswith("word/media/"):
                    continue
                if Path(member).suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                if len(results) >= MAX_EMBEDDED_IMAGES:
                    break
                results.append((archive.read(member), Path(member).name, None))
        return results

    return results


def _extract_pdf(file_bytes: bytes) -> List[ContentUnit]:
    """
    Extract PDF content page by page.

    Digital pages:
        Extract selectable text directly using PyMuPDF.

    Scanned pages:
        Render the page as an image and extract text using OCR.

    Hybrid PDFs:
        Each page is processed independently, allowing digital
        and scanned pages in the same document.
    """

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

        page_limit = min(
            document.page_count,
            MAX_PAGES,
        )

        if document.page_count > MAX_PAGES:

            logger.warning(
                "PDF has %s pages. Only the first %s will be processed.",
                document.page_count,
                MAX_PAGES,
            )

        page_records = []
        font_sizes = Counter()

        # ----------------------------------------------------
        # FIRST PASS: INSPECT EACH PDF PAGE
        # ----------------------------------------------------

        for page_index in range(page_limit):

            page = document[page_index]
            page_number = page_index + 1

            try:
                selectable_text = page.get_text(
                    "text"
                ).strip()

            except Exception as exc:

                logger.warning(
                    "Could not read selectable text on PDF page %s: %s",
                    page_number,
                    exc,
                )

                selectable_text = ""

            try:
                blocks = page.get_text(
                    "dict"
                ).get("blocks", [])

            except Exception as exc:

                logger.warning(
                    "Could not inspect text layout on PDF page %s: %s",
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

                    font_sizes[
                        round(line_size, 1)
                    ] += len(line_text)

                    page_lines.append({
                        "text": line_text,
                        "size": round(line_size, 1),
                    })

                    page_text_parts.append(line_text)

            try:
                has_raster_images = bool(
                    page.get_images(full=True)
                )

            except Exception:
                has_raster_images = False

            # A page with very little selectable text is
            # considered a scanned-page candidate.
            is_scanned = (
                len(selectable_text)
                < MIN_TEXT_CHARS_PER_PAGE
            )

            page_records.append({
                "page": page_number,
                "lines": page_lines,
                "text": "\n".join(page_text_parts),
                "selectable_text": selectable_text,
                "has_images": has_raster_images,
                "is_scanned": is_scanned,
                "page_object": page,
            })

        # ----------------------------------------------------
        # HEADING DETECTION FOR DIGITAL PDF PAGES
        # ----------------------------------------------------

        body_size = (
            font_sizes.most_common(1)[0][0]
            if font_sizes
            else None
        )

        heading_sizes = []

        if body_size is not None:

            heading_sizes = sorted(
                {
                    line["size"]
                    for record in page_records
                    for line in record["lines"]
                    if (
                        line["size"]
                        >= body_size * HEADING_SIZE_RATIO
                        and len(line["text"])
                        <= MAX_HEADING_CHARS
                        and len(line["text"].split())
                        <= MAX_HEADING_WORDS
                    )
                },
                reverse=True,
            )[:6]

        heading_levels = {
            size: index + 1
            for index, size in enumerate(heading_sizes)
        }

        # ----------------------------------------------------
        # SECOND PASS: EXTRACT EACH PAGE
        # ----------------------------------------------------

        for record in page_records:

            page_number = record["page"]
            page_object = record["page_object"]
            page_text = record["text"]

            # ------------------------------------------------
            # SCANNED PDF PAGE
            # ------------------------------------------------

            if record["is_scanned"]:

                ocr_blocks: List[Tuple[str, bool]] = []

                try:

                    if Image is None:
                        raise RuntimeError(
                            "Pillow is not installed. "
                            "Scanned-page OCR is unavailable."
                        )

                    pixmap = page_object.get_pixmap(
                        dpi=OCR_DPI,
                        alpha=False,
                    )

                    rendered_image = Image.frombytes(
                        "RGB",
                        (
                            pixmap.width,
                            pixmap.height,
                        ),
                        pixmap.samples,
                    )

                    ocr_blocks = _ocr_page_blocks(rendered_image)

                except Exception as exc:

                    logger.warning(
                        "Could not render/OCR scanned PDF page %s: %s",
                        page_number,
                        exc,
                    )

                if ocr_blocks:

                    for block_text, is_heading in ocr_blocks:
                        units.append(
                            _create_text_unit(
                                block_text,
                                page=page_number,
                                heading_level=1 if is_heading else None,
                                extraction_method="ocr",
                            )
                        )

                elif page_text.strip():

                    # Preserve selectable text if OCR fails.
                    units.append(
                        _create_text_unit(
                            page_text,
                            page=page_number,
                            extraction_method="digital",
                        )
                    )

                else:

                    logger.warning(
                        "Scanned PDF page %s produced no OCR text.",
                        page_number,
                    )

            # ------------------------------------------------
            # DIGITAL PDF PAGE
            # ------------------------------------------------

            else:

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
                                    extraction_method="digital",
                                )
                            )

                            normal_lines = []

                        units.append(
                            _create_text_unit(
                                line_text,
                                page=page_number,
                                heading_level=heading_level,
                                extraction_method="digital",
                            )
                        )

                    else:
                        normal_lines.append(line_text)

                if normal_lines:

                    units.append(
                        _create_text_unit(
                            "\n".join(normal_lines),
                            page=page_number,
                            extraction_method="digital",
                        )
                    )

            # ------------------------------------------------
            # EMBEDDED PDF IMAGE DESCRIPTIONS
            # ------------------------------------------------

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

                    extracted = document.extract_image(
                        xref
                    )

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


# ============================================================
# DOCX EXTRACTION
# ============================================================

_HEADING_STYLE = re.compile(
    r"^heading\s*([1-6])$",
    re.IGNORECASE,
)


def _docx_heading_level(
    paragraph: Any,
) -> Optional[int]:
    """Identify heading levels from Word paragraph styles."""

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
    """Extract DOCX image descriptions and available metadata."""

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

            paragraph_text = _clean(
                block.text
            )

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

    # DOCX does not provide fixed page numbers.
    _extract_docx_images(
        document,
        units,
    )

    return units


# ============================================================
# STANDALONE IMAGE EXTRACTION
# ============================================================

def _extract_image(
    file_bytes: bytes,
) -> List[ContentUnit]:
    """Extract image information and OCR text."""

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

    # OCR text is stored separately from the image unit.
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


# ============================================================
# PUBLIC API - USED BY ORCHESTRATOR
# ============================================================

def extract_units(
    *,
    text: Optional[str],
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> List[ContentUnit]:
    """
    Extract content from pasted text or an uploaded file.

    This function preserves the existing interface expected
    by backend/app/services/orchestrator.py.

    Inputs:
        text:
            Pasted text.

        file_name:
            Uploaded file name.

        file_bytes:
            Uploaded file content.

    Returns:
        List[ContentUnit]

    Raises:
        UnsupportedFormatError:
            If the file format is unsupported, corrupt,
            or a required dependency is missing.
    """

    # --------------------------------------------------------
    # PASTED TEXT
    # --------------------------------------------------------

    if text is not None:

        paragraphs = _split_paragraphs(
            text
        )

        return [
            _create_text_unit(paragraph)
            for paragraph in paragraphs
        ]

    # --------------------------------------------------------
    # UPLOADED FILE
    # --------------------------------------------------------

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


# ============================================================
# BACKWARDS-FRIENDLY ALIAS
# ============================================================

def extract_document(
    *,
    text: Optional[str] = None,
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
) -> List[ContentUnit]:
    """Alias for extract_units()."""

    return extract_units(
        text=text,
        file_name=file_name,
        file_bytes=file_bytes,
    )
