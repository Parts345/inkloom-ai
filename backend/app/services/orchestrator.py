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
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from uuid import uuid4

from app.schemas import AuditEvent, ContentUnit, Finding, SubmissionResponse, UnitMetadata
from app.services import deterministic, extraction, persistence, verification

log = logging.getLogger(__name__)

VERIFICATION_TIMEOUT_S = 45.0
SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


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


def _extract_with_image_support(
    *,
    text: Optional[str],
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> Tuple[List[ContentUnit], Optional[str]]:
    """Delegate to Riyesh's extraction.py, with direct image unit fallback for PNG/JPG uploads."""
    ext = Path(file_name).suffix.lower() if file_name else ""
    if file_bytes and ext in IMAGE_EXTENSIONS:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.write(file_bytes)
        tmp.close()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        unit = ContentUnit(
            unit_id="img_1",
            unit_type="image",
            content=tmp.name,
            metadata=UnitMetadata(image_path=tmp.name, mime_type=mime),
        )
        return [unit], tmp.name

    units = extraction.extract_units(text=text, file_name=file_name, file_bytes=file_bytes)
    return list(units), None


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

    temp_image_path: Optional[str] = None
    try:
        # 1. Extraction (supports digital text, OCR scanned PDFs, and direct image uploads)
        try:
            units, temp_image_path = await asyncio.to_thread(
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
        llm_findings = _settle(results[1], run, "llm_verification")
        vlm_findings = _settle(results[2], run, "vision_verification") if image_units else []

        # 3. Defensive unit filter + merge duplicates
        raw = det_findings + llm_findings + vlm_findings
        candidates = [f for f in raw if f.unit_id in unit_ids]
        if len(candidates) != len(raw):
            run.log("findings_dropped_unknown_unit", f"count={len(raw) - len(candidates)}")

        merged = merge_findings(candidates)
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
        )
    finally:
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
            except OSError:
                pass


def merge_findings(findings: Iterable[Finding]) -> List[Finding]:
    """Collapse findings flagging the same span, category, and unit."""
    groups: dict[Tuple[str, str, str], List[Finding]] = {}
    for f in findings:
        key = (f.unit_id, f.category, _normalise(f.original_span))
        groups.setdefault(key, []).append(f)

    merged = [_merge_group(group) for group in groups.values()]
    merged.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.unit_id, f.finding_id))
    return merged


def _merge_group(group: List[Finding]) -> Finding:
    best = max(group, key=lambda f: (SEVERITY_RANK[f.severity], f.confidence))

    explanations: List[str] = []
    for f in group:
        if f.explanation and f.explanation not in explanations:
            explanations.append(f.explanation)

    return best.model_copy(
        update={
            "confidence": max(f.confidence for f in group),
            "explanation": " ".join(explanations),
            "evidence": best.evidence or next((f.evidence for f in group if f.evidence), None),
            "suggested_edit": best.suggested_edit
            or next((f.suggested_edit for f in group if f.suggested_edit), None),
            "visual_notes": best.visual_notes
            or next((f.visual_notes for f in group if f.visual_notes), None),
        }
    )


def _settle(result: object, run: _Run, stage: str) -> List[Finding]:
    if isinstance(result, BaseException):
        reason = "timeout" if isinstance(result, TimeoutError) else type(result).__name__
        log.warning("%s degraded: %s", stage, result, exc_info=result)
        run.log(f"{stage}_degraded", reason)
        return []
    return list(result)  # type: ignore[arg-type]


def _initial_status(findings: List[Finding]) -> str:
    has_open_critical = any(f.severity == "critical" and f.status == "open" for f in findings)
    return "draft" if has_open_critical else "in_review"


def _joined_text(units: List[ContentUnit]) -> str:
    return "\n\n".join(u.content for u in units if u.unit_type == "text")


def _normalise(span: str) -> str:
    return " ".join(span.split()).casefold()