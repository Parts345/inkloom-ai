from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import faiss
import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import models as db_models
from app import schemas as shared_schemas
from app.database import SessionLocal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Local contracts
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


class _WorkspaceScoped(BaseModel):
    """Forces workspace IDs to str so int-typed DB rows can't break validation."""

    @field_validator("workspace_id", mode="before", check_fields=False)
    @classmethod
    def _coerce_workspace_id(cls, v: Any) -> str:
        if v is None:
            raise ValueError("workspace_id is required")
        return str(v)


class KnowledgeEntry(_WorkspaceScoped):
    id: str
    workspace_id: str
    entry_type: EntryType
    category: str
    title: str
    content: str
    preferred_replacement: Optional[str] = None
    severity: Severity = Severity.MAJOR
    source: Optional[str] = None
    expiry_date: Optional[date] = None
    is_active: bool = True


class EvidenceItem(_WorkspaceScoped):
    evidence_id: str
    workspace_id: str
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


class ContentUnit(_WorkspaceScoped):
    unit_id: str
    workspace_id: str
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
    suggested_edit: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(float(v), 3)

    @field_validator("fact_status", mode="before")
    @classmethod
    def _null_fact_status(cls, v: Any) -> Any:
        return FactVerificationStatus.NOT_APPLICABLE if v is None else v

    @field_validator("suggested_edit", "explanation", mode="before")
    @classmethod
    def _null_text(cls, v: Any) -> str:
        return "" if v is None else v


class LLMAnalysisResponse(BaseModel):
    findings: List[Finding] = Field(default_factory=list)


def _entry_to_evidence(
    entry: KnowledgeEntry, today: date, similarity: float = 0.0
) -> EvidenceItem:
    return EvidenceItem(
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
        is_expired=bool(entry.expiry_date and entry.expiry_date < today),
        similarity_score=similarity,
    )


# ---------------------------------------------------------------------------
# Component 5: Workspace-scoped FAISS retrieval
# ---------------------------------------------------------------------------


class BrandRAGEngine:
    """Workspace-scoped vector index (all-MiniLM-L6-v2 + FAISS).

    The encoder is loaded lazily so importing this module never fails, and
    entries are kept per workspace even when embedding fails, so lexical
    checks still work.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._lock = threading.RLock()
        self._model_name = model_name
        self._encoder: Any = None
        self._embedding_dim: int = 384
        self._entries: Dict[str, List[KnowledgeEntry]] = {}
        self._indices: Dict[str, Tuple[faiss.IndexFlatIP, List[KnowledgeEntry]]] = {}

    def _get_encoder(self) -> Any:
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading SentenceTransformer '%s' on CPU...", self._model_name)
            self._encoder = SentenceTransformer(self._model_name, device="cpu")
            get_dim = getattr(self._encoder, "get_embedding_dimension", None) or self._encoder.get_sentence_embedding_dimension
            self._embedding_dim = get_dim()
        return self._encoder

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

    def has_workspace(self, workspace_id: str) -> bool:
        with self._lock:
            return str(workspace_id) in self._entries

    def get_entries(self, workspace_id: str) -> List[KnowledgeEntry]:
        with self._lock:
            return list(self._entries.get(str(workspace_id), []))

    def rebuild_workspace_index(
        self, workspace_id: str, entries: List[KnowledgeEntry]
    ) -> int:
        ws = str(workspace_id)
        active = [e for e in entries if str(e.workspace_id) == ws and e.is_active]
        with self._lock:
            self._entries[ws] = active  # empty list still marks the workspace loaded
            if not active:
                self._indices.pop(ws, None)
                return 0
            try:
                encoder = self._get_encoder()
                texts = [self._format_entry(e) for e in active]
                embeddings = encoder.encode(
                    texts,
                    batch_size=32,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ).astype(np.float32)
                index = faiss.IndexFlatIP(self._embedding_dim)
                index.add(embeddings)
                self._indices[ws] = (index, active)
            except Exception:
                logger.exception("Embedding failed for workspace %s; lexical checks only", ws)
                self._indices.pop(ws, None)
            return len(active)

    def rebuild_all_indices(self, all_entries: List[KnowledgeEntry]) -> None:
        grouped: Dict[str, List[KnowledgeEntry]] = {}
        for entry in all_entries:
            grouped.setdefault(str(entry.workspace_id), []).append(entry)
        with self._lock:
            self._entries.clear()
            self._indices.clear()
            for ws_id, ws_entries in grouped.items():
                self.rebuild_workspace_index(ws_id, ws_entries)

    def retrieve_evidence(
        self,
        query_text: str,
        workspace_id: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        min_similarity: float = 0.20,
        reference_date: Optional[date] = None,
    ) -> List[EvidenceItem]:
        if not query_text or not query_text.strip():
            return []

        ws = str(workspace_id)
        today = reference_date or datetime.now(timezone.utc).date()

        with self._lock:
            if ws not in self._indices:
                return []
            index, entries = self._indices[ws]
            total = len(entries)
            if total == 0:
                return []

            query_vec = self._get_encoder().encode(
                [query_text.strip()],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).astype(np.float32)

            candidate_k = min(total, max(top_k * 3, 15))
            scores, indices = index.search(query_vec, candidate_k)

            results: List[EvidenceItem] = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= total:
                    continue
                clamped = float(max(0.0, min(1.0, score)))
                if clamped < min_similarity:
                    continue
                entry = entries[idx]
                if category_filter and entry.category.lower() != category_filter.lower():
                    continue
                results.append(_entry_to_evidence(entry, today, round(clamped, 4)))

        results.sort(key=lambda item: (item.is_expired, -item.similarity_score))
        return results[:top_k]


rag_engine = BrandRAGEngine()


# ---------------------------------------------------------------------------
# Components 6, 7 & 9: Claim / tone / terminology verification
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
4. Explanations must be written for a human reviewer. Do not mention "fact entry", "register", "evidence_id" or other internal terms. Say what is wrong and what the approved value is.
5. suggested_edit must not contain any banned term, and must not repeat the original text unchanged. If the correct action is removal, set suggested_edit to an empty string.
6. Prefer approved facts over invented wording. For example use "99.95% monthly uptime SLA" rather than "guaranteed" or "100% uptime".
7. suggested_edit must be plain text that can replace original_span inside a sentence. Do not add markup, URLs, file names, or new factual claims. For an image placeholder, suggest a short plain-text description such as "[Image: NovaTech team photo]". For a superlative or unsupported claim, suggest removing the claim or rewording it without adding a new claim.
8. Flag exclamation marks in customer-facing copy as tone issues under the brand voice guideline. Do not flag them in headings.
9. For punctuation issues such as exclamation marks, original_span must include the two or three words before the mark, for example "zero downtime!", and suggested_edit must be the same words without the mark. Never return a bare punctuation mark as original_span or suggested_edit. For passive voice, rewrite the sentence in active voice without dropping the subject. For example, "keeps your data encrypted by us" becomes "we encrypt your data".
10. Return ONLY valid JSON matching:
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


# ---- Deterministic fallback helpers ---------------------------------------

# Superlatives and guarantees the mock flags when no active fact supports them.
_UNSUPPORTED_CLAIM_RE = re.compile(
    r"(?<!\w)(?:#1|number one|best[- ]in[- ]class|guaranteed|fastest|unrivaled"
    r"|zero downtime|\d+(?:\.\d+)?%\s+(?:uptime|availability|accurate))(?!\w)",
    re.IGNORECASE,
)

# Numeric tokens such as "30", "99.9", "30%". Lookbehind stops "3.14" matching "14".
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?%?(?:\s+(?:days?|weeks?|months?|years?|hours?|minutes?|seconds?))?(?!\w)",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(never|not|no|cannot|must not|do not|don't)\b", re.IGNORECASE)


def _is_supporting_fact(e: EvidenceItem) -> bool:
    return (
        e.entry_type == EntryType.FACT
        and not e.is_expired
        and not _NEGATION_RE.search(e.quote)
    )


def _phrase_pattern(phrase: str) -> str:
    """Whole-phrase pattern that works for phrases starting or ending with symbols."""
    return rf"(?<![\w.]){re.escape(phrase)}(?!\w)"


def _mask_phrases(text: str, phrases: List[str]) -> str:
    """Blank out protected phrases while keeping string length, so offsets still line up."""
    masked = text
    for phrase in phrases:
        if phrase:
            masked = re.sub(
                _phrase_pattern(phrase),
                lambda m: " " * len(m.group(0)),
                masked,
                flags=re.IGNORECASE,
            )
    return masked


def _already_flagged(findings: List[Finding], span: str, category: IssueCategory) -> bool:
    return any(f.original_span == span and f.category == category for f in findings)


def _terminology_explanation(span: str, ev: EvidenceItem, suggested: str) -> str:
    text = f"'{span}' conflicts with approved terminology: {ev.quote}"
    if not suggested:
        text += " Remove the banned term."
    return text


def _deterministic_mock_analyze(
    unit: ContentUnit, evidence_pool: List[EvidenceItem]
) -> List[Finding]:
    """Fallback used when LLM_API_KEY is unset or the provider call fails.

    `evidence_pool` should include lexical matches across the whole workspace,
    not just semantically retrieved items, so key terms are caught reliably.
    """
    findings: List[Finding] = []
    text = unit.text

    # 1. Banned terms and preferred-term variants (terminology)
    for ev in evidence_pool:
        if ev.entry_type == EntryType.BANNED_TERM:
            term = ev.title.strip()
            protected: List[str] = []
            suggested = ev.preferred_replacement or ""
        elif (
            ev.entry_type == EntryType.PREFERRED_TERM
            and ev.preferred_replacement
            and "->" in ev.title
        ):
            # Title format "<non-preferred> -> <preferred>". Flag the variant,
            # but not occurrences that sit inside the preferred form.
            term = ev.title.split("->", 1)[0].strip()
            protected = [ev.preferred_replacement, f"NovaTech {term}"]
            suggested = ev.preferred_replacement
        else:
            continue

        if not term:
            continue

        haystack = _mask_phrases(text, protected)
        m = re.search(_phrase_pattern(term), haystack, flags=re.IGNORECASE)
        if not m:
            continue
        span = text[m.start():m.end()]
        if _already_flagged(findings, span, IssueCategory.TERMINOLOGY):
            continue

        findings.append(
            Finding(
                category=IssueCategory.TERMINOLOGY,
                fact_status=FactVerificationStatus.NOT_APPLICABLE,
                severity=ev.severity,
                original_span=span,
                evidence_id=ev.evidence_id,
                evidence_quote=ev.quote,
                explanation=_terminology_explanation(span, ev, suggested),
                suggested_edit=suggested,
                confidence=0.98,
            )
        )

    # 2. Numeric tokens from expired facts (outdated promotions/figures)
    for ev in evidence_pool:
        if not (ev.entry_type == EntryType.FACT and ev.is_expired):
            continue
        for tok in sorted(set(_NUMERIC_TOKEN_RE.findall(ev.quote))):
            m = re.search(_phrase_pattern(tok), text, flags=re.IGNORECASE)
            if not m:
                continue
            span = text[m.start():m.end()]
            if _already_flagged(findings, span, IssueCategory.FACT):
                continue
            findings.append(
                Finding(
                    category=IssueCategory.FACT,
                    fact_status=FactVerificationStatus.OUTDATED,
                    severity=Severity.CRITICAL,
                    original_span=span,
                    evidence_id=ev.evidence_id,
                    evidence_quote=ev.quote,
                    explanation=(
                        f"'{span}' refers to an offer or fact that ended on "
                        f"{ev.expiry_date.isoformat() if ev.expiry_date else 'an earlier date'}. "
                        "Confirm the offer is removed or updated."
                    ),
                    suggested_edit="",
                    confidence=0.90,
                )
            )
            break  # one finding per expired entry, same as the original behaviour

    # 3. Superlatives and guarantees with no active supporting fact
    active_facts = " ".join(
        e.quote.lower() for e in evidence_pool if _is_supporting_fact(e)
    )
    for m in _UNSUPPORTED_CLAIM_RE.finditer(text):
        span = m.group(0)
        if span.lower() in active_facts or any(f.original_span == span for f in findings):
            continue
        findings.append(
            Finding(
                category=IssueCategory.FACT,
                fact_status=FactVerificationStatus.UNSUPPORTED,
                severity=Severity.MAJOR,
                original_span=span,
                evidence_id=None,
                evidence_quote=None,
                explanation=(
                    f"'{span}' is not backed by an approved fact. Substantiate it with approved evidence or remove it."
                ),
                suggested_edit="",
                confidence=0.85,
            )
        )

    return findings


# ---- LLM path --------------------------------------------------------------


_BLANKED_EDIT_PHRASES = ("guaranteed", "zero downtime", "100% uptime")
_REMOVE_SUFFIX = " Remove the banned term instead of replacing it."
_MARKUP_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
_FILENAME_RE = re.compile(r"\b[\w-]+\.(jpg|jpeg|png|gif|svg|pdf|html?)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _banned_terms(evidence_pool: List[EvidenceItem]) -> List[str]:
    """Lowercased titles of every banned-term entry in the pool."""
    return [
        ev.title.strip().casefold()
        for ev in evidence_pool
        if ev.entry_type == EntryType.BANNED_TERM and ev.title.strip()
    ]


def _clean_suggested_edit(f: Finding, banned_terms: List[str]) -> None:
    """Blank suggestions that change nothing or would reintroduce banned wording."""
    edit = (f.suggested_edit or "").strip()
    if not edit or edit == f.original_span.strip():
        f.suggested_edit = ""
        return

    if _MARKUP_RE.search(edit) or _FILENAME_RE.search(edit) or _URL_RE.search(edit):
        f.suggested_edit = ""
        return

    lowered = edit.casefold()
    reintroduces_banned = any(
        re.search(_phrase_pattern(term), lowered, flags=re.IGNORECASE)
        for term in banned_terms
    ) or any(
        phrase in lowered and phrase not in f.original_span.casefold()
        for phrase in _BLANKED_EDIT_PHRASES
    )

    if reintroduces_banned:
        f.suggested_edit = ""
        f.explanation = f"{f.explanation.rstrip()}{_REMOVE_SUFFIX}"
        return

    f.suggested_edit = edit


def _validate_and_ground(
    raw_findings: List[Finding],
    unit: ContentUnit,
    retrieved_evidence: List[EvidenceItem],
    banned_terms: Optional[List[str]] = None,
) -> List[Finding]:
    evidence_map = {e.evidence_id: e for e in retrieved_evidence}
    banned = list(banned_terms or []) + _banned_terms(retrieved_evidence)
    validated: List[Finding] = []
    seen = set()
    claimed_punctuation_positions = set()

    for f in raw_findings:
        if not f.original_span.strip():
            continue
        if f.original_span not in unit.text:
            match = re.search(re.escape(f.original_span), unit.text, flags=re.IGNORECASE)
            if match:
                f.original_span = match.group(0)
            else:
                continue

        if not re.search(r"[A-Za-z0-9]", f.original_span):
            occurrences = re.finditer(re.escape(f.original_span), unit.text)
            occurrence = next(
                (match for match in occurrences if match.start() not in claimed_punctuation_positions),
                None,
            )
            if occurrence is None:
                continue

            mark = occurrence.group(0)
            preceding_text = unit.text[:occurrence.start()].rstrip()
            sentence_start = max(
                preceding_text.rfind("."),
                preceding_text.rfind("?"),
                preceding_text.rfind("!"),
            ) + 1
            sentence_text = preceding_text[sentence_start:].strip()
            sentence_words = sentence_text.split()
            context_text = (
                sentence_text if len(sentence_words) <= 3 else " ".join(sentence_words[-2:])
            )
            words_match = re.search(r"(?:\S+\s+){0,2}\S*$", context_text)
            preceding_words = words_match.group(0).strip() if words_match else ""
            if not preceding_words:
                continue

            f.original_span = f"{preceding_words}{mark}"
            if not f.suggested_edit or not re.search(r"[A-Za-z0-9]", f.suggested_edit):
                f.suggested_edit = preceding_words
            claimed_punctuation_positions.add(occurrence.start())

        _clean_suggested_edit(f, banned)

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


def _parse_llm_payload(raw: str) -> LLMAnalysisResponse:
    """Parse a provider's JSON reply, tolerating nulls in optional text fields.

    Raises ValueError (JSONDecodeError and pydantic ValidationError both subclass it)
    so the caller can retry with the error included in the prompt.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM reply must be a JSON object")

    findings = data.get("findings") or []
    if not isinstance(findings, list):
        raise ValueError("'findings' must be a list")

    for item in findings:
        if isinstance(item, dict):
            if item.get("fact_status") is None:
                item["fact_status"] = "not_applicable"
            if item.get("suggested_edit") is None:
                item["suggested_edit"] = ""
            if item.get("explanation") is None:
                item["explanation"] = ""
    data["findings"] = findings
    return LLMAnalysisResponse.model_validate(data)


def _llm_analyze(
    client: LLMProviderClient,
    unit: ContentUnit,
    retrieved_evidence: List[EvidenceItem],
    max_retries: int,
    temperature: float,
    banned_terms: Optional[List[str]] = None,
) -> List[Finding]:
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
            parsed = _parse_llm_payload(raw)
            return _validate_and_ground(parsed.findings, unit, retrieved_evidence, banned_terms)
        except ValueError as err:  # JSON decode and schema errors
            last_err = err
            logger.warning("Retry %d/%d for unit %s: %s", attempt + 1, max_retries, unit.unit_id, err)
            prompt = f"{base_prompt}\n\nINVALID JSON OUTPUT:\n{err}\nReturn ONLY valid schema JSON."

    raise RuntimeError(f"Schema validation failed after {max_retries + 1} attempts: {last_err}")


def analyze_text_unit(
    unit: ContentUnit,
    retrieved_evidence: List[EvidenceItem],
    evidence_pool: Optional[List[EvidenceItem]] = None,
    max_retries: int = 2,
    temperature: float = 0.1,
) -> List[Finding]:
    """Analyze one ContentUnit.

    `retrieved_evidence` goes to the LLM. `evidence_pool` (defaults to
    `retrieved_evidence`) is used by the deterministic fallback.
    Any LLM failure, including an invalid key, falls back to the mock.
    """
    client = LLMProviderClient()
    path = "llm" if client.is_configured else "mock"
    logger.info("Verification path for unit %s: %s", unit.unit_id, path)
    if not unit.text or not unit.text.strip():
        return []

    pool = evidence_pool if evidence_pool is not None else retrieved_evidence
    if not client.is_configured:
        return _deterministic_mock_analyze(unit, pool)

    try:
        llm_findings = _llm_analyze(
            client, unit, retrieved_evidence, max_retries, temperature, _banned_terms(pool)
        )
    except Exception as err:  # auth errors, network errors, schema failures, missing SDK
        logger.exception(
            "LLM verification failed for unit %s (%s); using deterministic fallback",
            unit.unit_id,
            err,
        )
        return _deterministic_mock_analyze(unit, pool)

    # Keep exact-match checks that the LLM missed, but avoid duplicating a
    # deterministic span already covered by a broader LLM finding.
    mock_findings = _deterministic_mock_analyze(unit, pool)
    extra = [
        mock_finding
        for mock_finding in mock_findings
        if not any(
            mock_finding.original_span.lower() in llm_finding.original_span.lower()
            for llm_finding in llm_findings
        )
    ]
    return llm_findings + extra



# ---------------------------------------------------------------------------
# DB adapter (bridges app.schemas / app.models <-> BrandRAGEngine)
# ---------------------------------------------------------------------------

_SOURCE_TYPE_MAP: Dict[str, shared_schemas.SourceType] = {
    "guideline": "guideline",
    "fact": "fact",
    "preferred_term": "term",
    "banned_term": "term",
}


def _db_entry_to_rag_entry(row: db_models.KnowledgeEntry) -> KnowledgeEntry:
    raw_type = (row.entry_type or "guideline").strip().lower()
    try:
        entry_type = EntryType(raw_type)
    except ValueError:
        entry_type = EntryType.GUIDELINE

    raw_sev = (row.severity or "major").strip().lower()
    try:
        severity = Severity(raw_sev)
    except ValueError:
        severity = Severity.MAJOR

    title = row.title or ""
    preferred = None
    if entry_type == EntryType.PREFERRED_TERM and "->" in title:
        preferred = title.split("->", 1)[1].strip()

    return KnowledgeEntry(
        id=str(row.id),
        workspace_id=str(row.workspace_id),  # normalise int or str IDs
        entry_type=entry_type,
        category=row.category or "general",
        title=title,
        content=row.rule_text or "",
        preferred_replacement=preferred,
        severity=severity,
        source=row.source_doc,
        expiry_date=row.expiry_date,
        is_active=True,
    )


def rebuild_workspace_index(
    workspace_id: str, entries: Optional[List[db_models.KnowledgeEntry]] = None
) -> int:
    """Called on first query and whenever Admin adds a KnowledgeEntry."""
    ws = str(workspace_id)
    if entries is None:
        db = SessionLocal()
        try:
            rows = (
                db.query(db_models.KnowledgeEntry)
                .filter(db_models.KnowledgeEntry.workspace_id == ws)
                .all()
            )
            rag_entries = [_db_entry_to_rag_entry(r) for r in rows]
        finally:
            db.close()
    else:
        rag_entries = [_db_entry_to_rag_entry(e) for e in entries]

    return rag_engine.rebuild_workspace_index(ws, rag_entries)


def _ensure_index_loaded(workspace_id: str) -> None:
    if not rag_engine.has_workspace(workspace_id):
        rebuild_workspace_index(workspace_id)


def _to_shared_finding(
    local_f: Finding,
    unit_id: str,
    evidence_lookup: Dict[str, Tuple[EvidenceItem, Optional[str]]],
) -> shared_schemas.Finding:
    shared_ev: Optional[shared_schemas.EvidenceItem] = None
    if local_f.evidence_id and local_f.evidence_id in evidence_lookup:
        ev, owner = evidence_lookup[local_f.evidence_id]
        shared_ev = shared_schemas.EvidenceItem(
            evidence_id=ev.evidence_id,
            source_type=_SOURCE_TYPE_MAP.get(ev.entry_type.value, "guideline"),
            title=ev.title,
            content=ev.quote,
            owner=owner,
            expiry_date=ev.expiry_date,
        )

    return shared_schemas.Finding(
        finding_id=f"llm_{uuid4().hex[:16]}",
        unit_id=str(unit_id),
        category=local_f.category.value,  # type: ignore[arg-type]
        severity=local_f.severity.value,  # type: ignore[arg-type]
        original_span=local_f.original_span,
        explanation=local_f.explanation,
        evidence=shared_ev,
        suggested_edit=local_f.suggested_edit or None,
        confidence=local_f.confidence,
    )


async def verify_units(
    units: List[shared_schemas.ContentUnit], workspace_id: str
) -> List[shared_schemas.Finding]:
    """Batch entrypoint awaited by orchestrator.py."""
    ws = str(workspace_id)

    def _run_sync() -> List[shared_schemas.Finding]:
        _ensure_index_loaded(ws)
        today = datetime.now(timezone.utc).date()

        db = SessionLocal()
        try:
            owner_map = {
                str(row.id): row.owner
                for row in db.query(db_models.KnowledgeEntry)
                .filter(db_models.KnowledgeEntry.workspace_id == ws)
                .all()
            }
        finally:
            db.close()

        # Every active entry in the workspace, for lexical checks that don't depend on embeddings.
        lexical_all = [_entry_to_evidence(e, today) for e in rag_engine.get_entries(ws)]

        all_findings: List[shared_schemas.Finding] = []
        for unit in units:
            if unit.unit_type != "text" or not (unit.content or "").strip():
                continue

            try:
                retrieved = rag_engine.retrieve_evidence(
                    query_text=unit.content,
                    workspace_id=ws,
                    top_k=6,
                    min_similarity=0.15,
                    reference_date=today,
                )
            except Exception:
                logger.exception("Retrieval failed for unit %s; using lexical pool only", unit.unit_id)
                retrieved = []

            pool_map: Dict[str, EvidenceItem] = {ev.evidence_id: ev for ev in lexical_all}
            pool_map.update({ev.evidence_id: ev for ev in retrieved})
            ev_lookup = {
                eid: (ev, owner_map.get(eid)) for eid, ev in pool_map.items()
            }

            local_unit = ContentUnit(
                unit_id=str(unit.unit_id),
                workspace_id=ws,
                text=unit.content,
            )
            local_findings = analyze_text_unit(
                local_unit,
                retrieved,
                evidence_pool=list(pool_map.values()),
            )
            for lf in local_findings:
                all_findings.append(_to_shared_finding(lf, unit.unit_id, ev_lookup))

        return all_findings

    return await asyncio.to_thread(_run_sync)