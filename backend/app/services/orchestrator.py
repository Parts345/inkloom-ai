"""Central analysis orchestrator.

Pipeline:
    input -> extraction -> deterministic checks + LLM/RAG verification (concurrent)
          -> merge and deduplicate -> persist -> SubmissionResponse
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple
from uuid import uuid4

from app.schemas import AuditEvent, ContentUnit, Finding, SubmissionResponse
from app.services import deterministic, extraction, persistence, verification

log = logging.getLogger(__name__)

VERIFICATION_TIMEOUT_S = 120.0
SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}


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
    """Run the full analysis pipeline for one pasted text or uploaded file."""
    if bool(text) == bool(file_bytes):
        raise AnalysisInputError("Provide exactly one of pasted text or a file.")

    run = _Run(submission_id=submission_id or uuid4().hex, actor=actor_id)
    run.log("analysis_started", "source=file" if file_bytes else "source=text")

    # 1. Extraction is mandatory: without content units there is nothing to analyse.
    try:
        units: List[ContentUnit] = await asyncio.to_thread(
            extraction.extract_units, text=text, file_name=file_name, file_bytes=file_bytes
        )
    except extraction.UnsupportedFormatError as exc:
        raise AnalysisInputError(str(exc)) from exc
    if not units:
        raise AnalysisInputError("No analysable content was found in the input.")
    unit_ids = {u.unit_id for u in units}
    run.log("extraction_completed", f"units={len(units)}")

    # 2. Deterministic checks and LLM verification run concurrently.
    #    A failure in either stage degrades the result rather than failing the request.
    det_result, llm_result = await asyncio.gather(
        asyncio.to_thread(deterministic.run_checks, units, workspace_id),
        asyncio.wait_for(
            verification.verify_units(units, workspace_id), timeout=VERIFICATION_TIMEOUT_S
        ),
        return_exceptions=True,
    )
    det_findings = _settle(det_result, run, "deterministic_checks")
    llm_findings = _settle(llm_result, run, "llm_verification")

    # 3. Defensive: discard findings that point at a unit we did not extract.
    raw = det_findings + llm_findings
    candidates = [f for f in raw if f.unit_id in unit_ids]
    if len(candidates) != len(raw):
        run.log("findings_dropped_unknown_unit", f"count={len(raw) - len(candidates)}")

    # 4. Merge duplicates and persist.
    merged = merge_findings(candidates)
    run.log("findings_merged", f"raw={len(candidates)} merged={len(merged)}")

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
    )


def merge_findings(findings: Iterable[Finding]) -> List[Finding]:
    """Collapse findings flagging the same span, category and unit.

    Keeps the most severe, most confident finding; combines explanations; retains the
    first available evidence and suggested edit.
    """
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
        }
    )


def _settle(result: object, run: _Run, stage: str) -> List[Finding]:
    """Return a stage's findings, or an empty list with an audit entry if it failed."""
    if isinstance(result, BaseException):
        reason = (
            f"timeout after {VERIFICATION_TIMEOUT_S}s"
            if isinstance(result, TimeoutError)
            else f"{type(result).__name__}: {str(result)[:200]}"
        )
        log.warning("%s degraded: %s", stage, result, exc_info=result)
        run.log(f"{stage}_degraded", reason)
        return []
    return list(result)  # type: ignore[arg-type]


def _initial_status(findings: List[Finding]) -> str:
    # Proposal decision rule: open critical issues block review.
    has_open_critical = any(f.severity == "critical" and f.status == "open" for f in findings)
    return "draft" if has_open_critical else "in_review"


def _joined_text(units: List[ContentUnit]) -> str:
    return "\n\n".join(u.content for u in units if u.unit_type == "text")


def _normalise(span: str) -> str:
    return " ".join(span.split()).casefold()
