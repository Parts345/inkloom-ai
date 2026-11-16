"""Owner: Riyesh. Stub: pasted text is split into paragraph units. Files are not yet supported."""
from typing import List, Optional

from app.schemas import ContentUnit


class UnsupportedFormatError(ValueError):
    pass


def extract_units(
    *, text: Optional[str], file_name: Optional[str], file_bytes: Optional[bytes]
) -> List[ContentUnit]:
    if file_bytes is not None:
        raise UnsupportedFormatError(f"File extraction not implemented yet: {file_name}")
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return [
        ContentUnit(unit_id=f"u{i}", unit_type="text", content=p)
        for i, p in enumerate(paragraphs, start=1)
    ]
