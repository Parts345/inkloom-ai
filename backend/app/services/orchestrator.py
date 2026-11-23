"""Owner: Sanket / Soham. Central concurrent analysis orchestrator (Round 3).

Pipeline:
    input -> extraction (digital + OCR)
          -> concurrent checks via asyncio.gather:
               1. deterministic.run_checks (text + image rules)
               2. verification.verify_units (RAG + LLM text verification)
               3. vision_verifier.verify_images (VLM vs Approved Asset Library)
          -> merge & deduplicate -> persist -> SubmissionResponse
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union
from uuid import uuid4

from app.schemas import AuditEvent, ContentUnit, Finding, SubmissionResponse, UnitMetadata
from app.services import deterministic, extraction, persistence, verification

log = logging.getLogger(__name__)

VERIFICATION_TIMEOUT_S = 90.0
SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}
_SENTENCE_BREAK = re.compile(r"(?<=[.!?:])\s+")
_TRAILING_PUNCT = "!?.,:;"


class AnalysisInputError(ValueError):
    """Bad input from the client. The API maps this to HTTP 422."""


@dataclass
class _Run:
    submission_id: str
    actor: str
    audit: List[AuditEvent] = field(default_factory=list)

    def log(self, action: str, detail: Optional[str] = None) -> None:
        self.audit.append(
            AuditEvent(
                event_id=uuid4().hex,
                submission_id=self.submission_id,
                timestamp=datetime.now(timezone.utc),
                actor=self.actor,
                action=action,
                detail=detail,
            )
        )


def _write_temp_image(data: bytes, suffix: str) -> str:
    """Write image bytes to a temp file for the vision checks and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".png")
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name


def _embedded_image_units(file_bytes: bytes, file_name: Optional[str]) -> Tuple[List[ContentUnit], List[str]]:
    """Create one image unit per picture embedded in a PDF or DOCX, each with its own temp file."""
    units: List[ContentUnit] = []
    temp_paths: List[str] = []
    for data, name, page in extraction.extract_embedded_images(file_bytes, file_name):
        try:
            path = _write_temp_image(data, Path(name).suffix.lower())
        except OSError as exc:
            log.warning("Could not store embedded image %s: %s", name, exc)
            continue
        temp_paths.append(path)

        unit = extraction._create_image_unit(f"Embedded image: {name}", page=page)
        unit.metadata.image_path = path
        unit.metadata.mime_type = mimetypes.guess_type(name)[0] or "image/png"
        unit.metadata.source_name = name
        unit.metadata.embedded = True
        units.append(unit)
    return units, temp_paths


def _extract_with_image_support(
    *,
    text: Optional[str],
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> Tuple[List[ContentUnit], List[str]]:
    """Run extraction, then give image units temp files for the vision checks.

    Standalone images go through extraction.extract_units, so OCR text is checked like any other
    text. Pictures embedded in PDFs and DOCX files are added as image units too. Temp file paths
    are stored only in metadata. They never become unit content, so they cannot appear in a
    finding's original_span or in the reviewed draft. Returns every temp path so the caller can
    delete them.
    """
    units = list(extraction.extract_units(text=text, file_name=file_name, file_bytes=file_bytes))
    temp_paths: List[str] = []

    if not file_bytes:
        return units, temp_paths

    ext = Path(file_name).suffix.lower() if file_name else ""

    if ext in extraction.IMAGE_EXTENSIONS:
        path = _write_temp_image(file_bytes, ext)
        temp_paths.append(path)
        mime = mimetypes.guess_type(file_name or "")[0] or "image/jpeg"
        for unit in units:
            if unit.unit_type == "image":
                unit.metadata.image_path = path
                unit.metadata.mime_type = mime
                unit.metadata.source_name = Path(file_name).name
        return units, temp_paths

    if ext in (".pdf", ".docx"):
        try:
            embedded_units, embedded_paths = _embedded_image_units(file_bytes, file_name)
        except Exception:
            log.warning("Embedded image extraction failed; continuing with text only.", exc_info=True)
            embedded_units, embedded_paths = [], []
        units.extend(embedded_units)
        temp_paths.extend(embedded_paths)

    return units, temp_paths


async def _run_vision_verification(
    image_units: List[ContentUnit],
    workspace_id: str,
    run: _Run,
) -> List[Finding]:
    """Call Ushank's vision_verifier.verify_images(...) concurrently if available."""
    try:
        from app.services import vision_verifier
    except ImportError:
        run.log("vision_verification_skipped", "vision_verifier module not yet installed")
        return []

    verify_fn = getattr(vision_verifier, "verify_images", None)
    if not callable(verify_fn):
        run.log("vision_verification_skipped", "vision_verifier.verify_images not defined")
        return []

    if inspect.iscoroutinefunction(verify_fn):
        findings = await verify_fn(image_units, workspace_id)
    else:
        findings = await asyncio.to_thread(verify_fn, image_units, workspace_id)

    result_list = list(findings)
    run.log(
        "vision_verification_completed",
        f"images={len(image_units)} findings={len(result_list)}",
    )
    return result_list


async def analyse_submission(
    *,
    workspace_id: str,
    actor_id: str,
    title: str,
    text: Optional[str] = None,
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    submission_id: Optional[str] = None,
) -> SubmissionResponse:
    """Run the full multimodal analysis pipeline for pasted text, documents, or images."""
    if bool(text) == bool(file_bytes):
        raise AnalysisInputError("Provide exactly one of pasted text or a file.")

    run = _Run(submission_id=submission_id or uuid4().hex, actor=actor_id)
    source_format = Path(file_name).suffix.lower().lstrip(".") if file_name else "text"
    run.log("analysis_started", f"source={source_format}")

    temp_image_paths: List[str] = []
    try:
        # 1. Extraction (supports digital text, OCR scanned PDFs, and direct image uploads)
        try:
            units, temp_image_paths = await asyncio.to_thread(
                _extract_with_image_support,
                text=text,
                file_name=file_name,
                file_bytes=file_bytes,
            )
        except extraction.UnsupportedFormatError as exc:
            raise AnalysisInputError(str(exc)) from exc

        if not units:
            raise AnalysisInputError("No analysable content was found in the input.")

        unit_ids = {u.unit_id for u in units}
        ocr_units = sum(1 for u in units if u.metadata.is_ocr)
        image_units = [u for u in units if u.unit_type == "image"]
        run.log(
            "extraction_completed",
            f"units={len(units)} ocr_units={ocr_units} image_units={len(image_units)}",
        )

        # 2. Concurrent execution: Deterministic + Text RAG/LLM + VLM Image Verification
        tasks = [
            asyncio.to_thread(deterministic.run_checks, units, workspace_id),
            asyncio.wait_for(
                verification.verify_units(units, workspace_id), timeout=VERIFICATION_TIMEOUT_S
            ),
        ]
        if image_units:
            tasks.append(
                asyncio.wait_for(
                    _run_vision_verification(image_units, workspace_id, run),
                    timeout=VERIFICATION_TIMEOUT_S,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        det_findings = _settle(results[0], run, "deterministic_checks")
        if isinstance(results[1], verification.ClaimChecksUnavailable):
            run.log(
                "llm_verification_failed",
                f"claim checks failed for {len(results[1].failed_unit_ids)} unit(s); rule-based results kept",
            )
            llm_findings = list(results[1].partial_findings)
        else:
            llm_findings = _settle(results[1], run, "llm_verification")
        vlm_findings = _settle(results[2], run, "vision_verification") if image_units else []
        llm_verification_failed = isinstance(results[1], BaseException)

        # 3. Defensive unit filter + merge duplicates
        raw = det_findings + llm_findings + vlm_findings
        candidates = [f for f in raw if f.unit_id in unit_ids]
        if len(candidates) != len(raw):
            run.log("findings_dropped_unknown_unit", f"count={len(raw) - len(candidates)}")

        merged = merge_findings(candidates, units)
        run.log("findings_merged", f"raw={len(candidates)} merged={len(merged)}")

        # 4. Persist to SQLAlchemy
        return persistence.save_analysis(
            submission_id=run.submission_id,
            workspace_id=workspace_id,
            title=title,
            actor_id=actor_id,
            status=_initial_status(merged),
            current_text=_joined_text(units),
            units=units,
            findings=merged,
            audit_events=run.audit,
            source_format=source_format or "text",
            llm_verification_failed=llm_verification_failed,
        )
    finally:
        for temp_path in temp_image_paths:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def merge_findings(
    findings: Iterable[Finding],
    units: Optional[Union[Iterable[ContentUnit], dict[str, str]]] = None,
) -> List[Finding]:
    """Collapse duplicate and overlapping findings on the same unit or identical unit texts."""
    candidates = list(findings)
    groups: List[List[Finding]] = []
    unassigned = set(range(len(candidates)))

    unit_texts: dict[str, str] = {}
    if units is not None:
        if isinstance(units, dict):
            unit_texts = units
        else:
            unit_texts = {u.unit_id: (u.content or "") for u in units}

    while unassigned:
        seed = unassigned.pop()
        group_indices = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            for other in tuple(unassigned):
                if _findings_overlap(candidates[current], candidates[other], unit_texts):
                    unassigned.remove(other)
                    group_indices.add(other)
                    pending.append(other)
        groups.append([candidates[index] for index in sorted(group_indices)])

    merged = [_merge_group(group) for group in groups]
    merged.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.unit_id, f.finding_id))
    return merged


def _findings_overlap(
    left: Finding,
    right: Finding,
    unit_texts: Optional[dict[str, str]] = None,
) -> bool:
    same_unit = left.unit_id == right.unit_id
    if not same_unit:
        if unit_texts and left.unit_id in unit_texts and right.unit_id in unit_texts:
            left_text = unit_texts[left.unit_id].strip()
            right_text = unit_texts[right.unit_id].strip()
            if not left_text or left_text != right_text:
                return False
        else:
            return False

    left_span = left.original_span.strip()
    right_span = right.original_span.strip()
    if not left_span or not right_span:
        return False

    left_image = left_span.casefold().startswith("[image:")
    right_image = right_span.casefold().startswith("[image:")
    if (left.category == "visual") != (right.category == "visual"):
        return False
    if left_image != right_image:
        return False

    left_clean = _normalise(left_span.rstrip(_TRAILING_PUNCT))
    right_clean = _normalise(right_span.rstrip(_TRAILING_PUNCT))

    spans_overlap = (
        left_clean == right_clean
        or (bool(left_clean) and bool(right_clean) and (left_clean in right_clean or right_clean in left_clean))
        or _normalise(left_span) == _normalise(right_span)
        or left_span.casefold() in right_span.casefold()
        or right_span.casefold() in left_span.casefold()
    )
    if not spans_overlap:
        return False

    if left.category == right.category:
        return True
    return {left.category, right.category} == {"brand", "terminology"}


def _merge_group(group: List[Finding]) -> Finding:
    brand = next((f for f in group if f.category == "brand"), None)
    terminology = next((f for f in group if f.category == "terminology"), None)
    is_brand_term_merge = brand is not None and terminology is not None
    primary = brand if is_brand_term_merge else max(
        group,
        key=lambda f: (len(f.original_span), SEVERITY_RANK[f.severity], f.confidence),
    )
    explanation = _merge_explanations([f.explanation for f in group if f.explanation])
    suggested_edit = primary.suggested_edit
    if is_brand_term_merge:
        suggestion = terminology.suggested_edit.strip()
        if suggestion:
            explanation = f"{brand.explanation.rstrip()} Preferred terminology: {suggestion}."
        suggested_edit = terminology.suggested_edit

    return primary.model_copy(
        update={
            "severity": max(group, key=lambda f: SEVERITY_RANK[f.severity]).severity,
            "confidence": max(f.confidence for f in group),
            "explanation": explanation,
            "suggested_edit": suggested_edit,
            "evidence": primary.evidence or next((f.evidence for f in group if f.evidence), None),
            "visual_notes": primary.visual_notes
            or next((f.visual_notes for f in group if f.visual_notes), None),
        }
    )


def _merge_explanations(texts: List[str]) -> str:
    """Join explanations for one span, dropping sentences the reader has already seen.

    Two checks often word the same rule differently, so exact-match dedupe is not enough.
    A sentence is dropped when its normalised text already appears in the kept text.
    """
    kept: List[str] = []
    seen = ""
    for text in texts:
        for sentence in _SENTENCE_BREAK.split(text.strip()):
            sentence = sentence.strip()
            if not sentence:
                continue
            key = _normalise(sentence).rstrip(".!? ")
            if len(key) >= 12 and key in seen:
                continue
            kept.append(sentence)
            seen += f" {key}"
    return " ".join(kept)


def _settle(result: object, run: _Run, stage: str) -> List[Finding]:
    if isinstance(result, BaseException):
        reason = (
            f"timeout after {VERIFICATION_TIMEOUT_S}s"
            if isinstance(result, TimeoutError)
            else (f"{type(result).__name__}: {str(result)[:200]}" if str(result) else type(result).__name__)
        )
        log.warning("%s degraded: %s", stage, result, exc_info=result)
        run.log(f"{stage}_degraded", reason)
        if stage == "llm_verification":
            run.log("llm_verification_failed", reason)
        return []
    return list(result)  # type: ignore[arg-type]


def _initial_status(findings: List[Finding]) -> str:
    has_open_critical = any(f.severity == "critical" and f.status == "open" for f in findings)
    return "draft" if has_open_critical else "in_review"


def _joined_text(units: List[ContentUnit]) -> str:
    return "\n\n".join(u.content for u in units if u.unit_type == "text")


def _normalise(span: str) -> str:
    return " ".join(span.split()).casefold()