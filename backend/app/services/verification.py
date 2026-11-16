"""Owner: Ushank. Stub: RAG retrieval plus schema-validated LLM judgement goes here."""
from typing import List

from app.schemas import ContentUnit, Finding


async def verify_units(units: List[ContentUnit], workspace_id: str) -> List[Finding]:
    return []
