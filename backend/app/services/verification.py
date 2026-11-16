from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared Contracts (Move to app/schemas.py and import if Sanket has created them)
# ---------------------------------------------------------------------------


class EntryType(str, Enum):
    GUIDELINE = "guideline"
    FACT = "fact"
    PREFERRED_TERM = "preferred_term"
    BANNED_TERM = "banned_term"


class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class IssueCategory(str, Enum):
    FACT = "fact"
    TONE = "tone"
    TERMINOLOGY = "terminology"


class FactVerificationStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    OUTDATED = "outdated"
    NOT_APPLICABLE = "not_applicable"


class KnowledgeEntry(BaseModel):
    id: str
    workspace_id: int
    entry_type: EntryType
    category: str
    title: str
    content: str
    preferred_replacement: Optional[str] = None
    severity: Severity = Severity.MAJOR
    source: Optional[str] = None
    expiry_date: Optional[date] = None
    is_active: bool = True


class EvidenceItem(BaseModel):
    evidence_id: str
    workspace_id: int
    entry_type: EntryType
    category: str
    title: str
    quote: str
    preferred_replacement: Optional[str] = None
    severity: Severity
    source: Optional[str] = None
    expiry_date: Optional[date] = None
    is_expired: bool = False
    similarity_score: float = Field(..., ge=0.0, le=1.0)


class ContentUnit(BaseModel):
    unit_id: str
    workspace_id: int
    text: str
    section_context: Optional[str] = None


class Finding(BaseModel):
    category: IssueCategory
    fact_status: FactVerificationStatus = FactVerificationStatus.NOT_APPLICABLE
    severity: Severity
    original_span: str
    evidence_id: Optional[str] = None
    evidence_quote: Optional[str] = None
    explanation: str
    suggested_edit: str
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(float(v), 3)


class LLMAnalysisResponse(BaseModel):
    findings: List[Finding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Component 5: Brand Knowledge Base & FAISS Retrieval
# ---------------------------------------------------------------------------


class BrandRAGEngine:
    """Workspace-scoped in-memory vector index using all-MiniLM-L6-v2 + FAISS."""

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._lock = threading.RLock()
        logger.info("Loading SentenceTransformer '%s' on CPU...", model_name)
        self._encoder = SentenceTransformer(model_name, device="cpu")
        self._embedding_dim: int = self._encoder.get_sentence_embedding_dimension()
        self._indices: Dict[int, Tuple[faiss.IndexFlatIP, List[KnowledgeEntry]]] = {}

    @staticmethod
    def _format_entry(entry: KnowledgeEntry) -> str:
        parts = [
            f"[{entry.entry_type.value.upper()}]",
            f"Category: {entry.category}",
            f"Title: {entry.title}",
            f"Rule/Fact: {entry.content}",
        ]
        if entry.preferred_replacement:
            parts.append(f"Use instead: {entry.preferred_replacement}")
        return " | ".join(parts)

    def rebuild_workspace_index(
        self, workspace_id: int, entries: List[KnowledgeEntry]
    ) -> int:
        active_entries = [
            e for e in entries if e.workspace_id == workspace_id and e.is_active
        ]
        with self._lock:
            if not active_entries:
                self._indices.pop(workspace_id, None)
                return 0

            texts = [self._format_entry(e) for e in active_entries]
            embeddings = self._encoder.encode(
                texts,
                batch_size=32,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).astype(np.float32)

            index = faiss.IndexFlatIP(self._embedding_dim)
            index.add(embeddings)
            self._indices[workspace_id] = (index, active_entries)
            return len(active_entries)

    def rebuild_all_indices(self, all_entries: List[KnowledgeEntry]) -> None:
        grouped: Dict[int, List[KnowledgeEntry]] = {}
        for entry in all_entries:
            grouped.setdefault(entry.workspace_id, []).append(entry)
        with self._lock:
            self._indices.clear()
            for ws_id, ws_entries in grouped.items():
                self.rebuild_workspace_index(ws_id, ws_entries)

    def retrieve_evidence(
        self,
        query_text: str,
        workspace_id: int,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        min_similarity: float = 0.20,
        reference_date: Optional[date] = None,
    ) -> List[EvidenceItem]:
        if not query_text or not query_text.strip():
            return []

        today = reference_date or datetime.utcnow().date()

        with self._lock:
            if workspace_id not in self._indices:
                return []

            index, entries = self._indices[workspace_id]
            total_indexed = len(entries)
            if total_indexed == 0:
                return []

            query_vec = self._encoder.encode(
                [query_text.strip()],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).astype(np.float32)

            candidate_k = min(total_indexed, max(top_k * 3, 15))
            scores, indices = index.search(query_vec, candidate_k)

            results: List[EvidenceItem] = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= total_indexed:
                    continue
                clamped_score = float(max(0.0, min(1.0, score)))
                if clamped_score < min_similarity:
                    continue

                entry = entries[idx]
                if category_filter and entry.category.lower() != category_filter.lower():
                    continue

                is_expired = bool(entry.expiry_date and entry.expiry_date < today)
                results.append(
                    EvidenceItem(
                        evidence_id=entry.id,
                        workspace_id=entry.workspace_id,
                        entry_type=entry.entry_type,
                        category=entry.category,
                        title=entry.title,
                        quote=entry.content,
                        preferred_replacement=entry.preferred_replacement,
                        severity=entry.severity,
                        source=entry.source,
                        expiry_date=entry.expiry_date,
                        is_expired=is_expired,
                        similarity_score=round(clamped_score, 4),
                    )
                )

        # Valid entries rank above expired entries, then by similarity descending
        results.sort(key=lambda item: (item.is_expired, -item.similarity_score))
        return results[:top_k]


# Singleton imported by main.py (startup/seed) and orchestrator.py
rag_engine = BrandRAGEngine()


# ---------------------------------------------------------------------------
# Components 6, 7 & 9: Claim/Tone/Terminology Verification & Minimal Edits
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Inkloom AI's Brand Governance Verification Engine.
Audit the submitted ContentUnit strictly against RETRIEVED_EVIDENCE.

RULES:
1. EVIDENCE BEFORE GENERATION:
   - Never invent rules or verify facts from general pre-training knowledge.
   - Cite an exact `evidence_id` and `evidence_quote` from RETRIEVED_EVIDENCE, OR if a factual/numeric/superlative claim has no matching entry, set `evidence_id` to null, `fact_status` to "unsupported", and state that no supporting evidence was found in the approved fact register.
   - If a claim matches an entry with `is_expired == true`, classify `category` as "fact" and `fact_status` as "outdated".
   - If a claim contradicts an active (`is_expired == false`) fact, classify `category` as "fact" and `fact_status` as "conflicting".
   - Only return violations or unverified claims (omit `fact_status == "supported"`).
2. CATEGORIES:
   - `fact`: Numeric claims, specs, certifications, pricing, guarantees, or superlatives.
   - `tone`: Off-brand voice or stylistic violations against retrieved `guideline` entries.
   - `terminology`: Usage of `banned_term` or missing `preferred_term` from RETRIEVED_EVIDENCE.
3. MINIMAL EDITS:
   - `original_span` must be an exact verbatim substring of `content_unit.text`.
   - `suggested_edit` must be a minimal drop-in replacement for `original_span` preserving the author's meaning and voice.
4. Return ONLY valid JSON matching:
   {"findings": [{"category": "...", "fact_status": "...", "severity": "...", "original_span": "...", "evidence_id": null, "evidence_quote": null, "explanation": "...", "suggested_edit": "...", "confidence": 0.0}]}
"""


class LLMProviderClient:
    DEFAULT_CONFIGS: Dict[str, Dict[str, Optional[str]]] = {
        "openai": {"base_url": None, "model": "gpt-4o-mini"},
        "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "model": "gemini-2.5-flash",
        },
    }

    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
        self.api_key = os.getenv("LLM_API_KEY", "").strip()
        defaults = self.DEFAULT_CONFIGS.get(self.provider, self.DEFAULT_CONFIGS["openai"])
        self.base_url = os.getenv("LLM_BASE_URL", "").strip() or defaults["base_url"]
        self.model = os.getenv("LLM_MODEL", "").strip() or defaults["model"]

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or "{}"


def _deterministic_mock_analyze(
    unit: ContentUnit, retrieved_evidence: List[EvidenceItem]
) -> List[Finding]:
    """Fallback used when LLM_API_KEY is unset so local Docker/demo runs never crash."""
    findings: List[Finding] = []
    text = unit.text

    for ev in retrieved_evidence:
        if ev.entry_type == EntryType.BANNED_TERM:
            match = re.search(re.escape(ev.title.strip()), text, flags=re.IGNORECASE)
            if match:
                span = match.group(0)
                findings.append(
                    Finding(
                        category=IssueCategory.TERMINOLOGY,
                        fact_status=FactVerificationStatus.NOT_APPLICABLE,
                        severity=ev.severity,
                        original_span=span,
                        evidence_id=ev.evidence_id,
                        evidence_quote=ev.quote,
                        explanation=f"Banned term '{span}' violates [{ev.evidence_id}]: {ev.quote}",
                        suggested_edit=ev.preferred_replacement or "[approved term]",
                        confidence=0.98,
                    )
                )
        elif ev.entry_type == EntryType.FACT and ev.is_expired:
            for num in set(re.findall(r"\b\d+(?:\.\d+)?%?\b", ev.quote)):
                match = re.search(rf"\b{re.escape(num)}\b", text)
                if match:
                    span = match.group(0)
                    findings.append(
                        Finding(
                            category=IssueCategory.FACT,
                            fact_status=FactVerificationStatus.OUTDATED,
                            severity=Severity.CRITICAL,
                            original_span=span,
                            evidence_id=ev.evidence_id,
                            evidence_quote=ev.quote,
                            explanation=f"Figure '{span}' matches expired fact [{ev.evidence_id}] (expired {ev.expiry_date}).",
                            suggested_edit=f"[Verify current figure replacing {span}]",
                            confidence=0.90,
                        )
                    )
                    break

    active_facts = " ".join(
        e.quote.lower()
        for e in retrieved_evidence
        if e.entry_type == EntryType.FACT and not e.is_expired
    )
    for match in re.finditer(
        r"\b(#1|number one|best-in-class|guaranteed|100% accurate|fastest|unrivaled)\b",
        text,
        flags=re.IGNORECASE,
    ):
        span = match.group(0)
        if span.lower() not in active_facts and not any(f.original_span == span for f in findings):
            findings.append(
                Finding(
                    category=IssueCategory.FACT,
                    fact_status=FactVerificationStatus.UNSUPPORTED,
                    severity=Severity.MAJOR,
                    original_span=span,
                    evidence_id=None,
                    evidence_quote=None,
                    explanation=f"Claim '{span}' has no supporting evidence in the approved fact register.",
                    suggested_edit="leading",
                    confidence=0.85,
                )
            )
    return findings


def _validate_and_ground(
    raw_findings: List[Finding],
    unit: ContentUnit,
    retrieved_evidence: List[EvidenceItem],
) -> List[Finding]:
    evidence_map = {e.evidence_id: e for e in retrieved_evidence}
    validated: List[Finding] = []
    seen = set()

    for f in raw_findings:
        if f.original_span not in unit.text:
            match = re.search(re.escape(f.original_span), unit.text, flags=re.IGNORECASE)
            if match:
                f.original_span = match.group(0)
            else:
                continue

        if f.evidence_id is not None:
            ev = evidence_map.get(f.evidence_id)
            if ev is None:
                f.evidence_id = None
                f.evidence_quote = None
                if f.category == IssueCategory.FACT:
                    f.fact_status = FactVerificationStatus.UNSUPPORTED
            else:
                f.evidence_quote = ev.quote
                if ev.entry_type == EntryType.FACT and ev.is_expired:
                    f.category = IssueCategory.FACT
                    f.fact_status = FactVerificationStatus.OUTDATED

        key = (f.category, f.original_span, f.evidence_id)
        if key not in seen:
            seen.add(key)
            validated.append(f)

    return validated


def analyze_text_unit(
    unit: ContentUnit,
    retrieved_evidence: List[EvidenceItem],
    max_retries: int = 2,
    temperature: float = 0.1,
) -> List[Finding]:
    """Main entrypoint called by orchestrator.py for each extracted ContentUnit."""
    if not unit.text or not unit.text.strip():
        return []

    client = LLMProviderClient()
    if not client.is_configured:
        return _deterministic_mock_analyze(unit, retrieved_evidence)

    base_prompt = json.dumps(
        {
            "content_unit": unit.model_dump(),
            "retrieved_evidence": [e.model_dump(mode="json") for e in retrieved_evidence],
        },
        indent=2,
    )
    prompt = base_prompt
    last_err: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            raw = client.generate_json(SYSTEM_PROMPT, prompt, temperature=temperature)
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
            parsed = LLMAnalysisResponse.model_validate_json(cleaned)
            return _validate_and_ground(parsed.findings, unit, retrieved_evidence)
        except (json.JSONDecodeError, ValidationError) as err:
            last_err = err
            logger.warning("Retry %d/%d for unit %s: %s", attempt + 1, max_retries, unit.unit_id, err)
            prompt = f"{base_prompt}\n\nINVALID JSON OUTPUT:\n{err}\nReturn ONLY valid schema JSON."

    raise RuntimeError(f"Schema validation failed after {max_retries + 1} attempts: {last_err}")
