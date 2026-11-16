"""Owner: Riyesh. Stub: contrast, dimension, alt-text and banned-term checks go here."""
from typing import List

from app.schemas import ContentUnit, Finding


def run_checks(units: List[ContentUnit], workspace_id: str) -> List[Finding]:
    return []
