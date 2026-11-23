"""Generate smoke-test inputs for Round 3.

  scanned_sample.pdf  image-only PDF (no text layer), so OCR must read it. It contains
                      lines that should be flagged: "#1 cloud platform", "guaranteed",
                      "first 3 months ... free", "$19/month".
  off_brand_logo.png  campaign image with an off-palette background, for the VLM check.

Run from backend/:  python -m tests.fixtures.make_fixtures
"""
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent

SCAN_LINES = [
    "Q4 Enterprise Launch",
    "NovaTech Cloud is the world's #1 cloud platform.",
    "100% uptime, guaranteed.",
    "Sign up: first 3 months of the Business tier free.",
    "Starter for $19/month.",
]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def make_scanned_pdf(path: Path) -> None:
    img = Image.new("RGB", (1240, 200 + 90 * len(SCAN_LINES)), "white")
    draw = ImageDraw.Draw(img)
    font = _font(48)
    for i, line in enumerate(SCAN_LINES):
        draw.text((60, 60 + i * 90), line, fill="black", font=font)

    tmp_png = OUT / "_scan_tmp.png"
    img.save(tmp_png)
    doc = fitz.open()
    page = doc.new_page(width=img.width * 0.5, height=img.height * 0.5)
    page.insert_image(page.rect, filename=str(tmp_png))
    doc.save(str(path))
    doc.close()
    tmp_png.unlink()


def make_off_brand_logo(path: Path) -> None:
    img = Image.new("RGB", (512, 512), (255, 200, 0))  # off-palette yellow background
    ImageDraw.Draw(img).rectangle([128, 128, 384, 384], fill=(255, 0, 255))
    img.save(path)


if __name__ == "__main__":
    make_scanned_pdf(OUT / "scanned_sample.pdf")
    make_off_brand_logo(OUT / "off_brand_logo.png")
    print("wrote", OUT / "scanned_sample.pdf", "and", OUT / "off_brand_logo.png")
