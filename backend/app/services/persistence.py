"""Owner: Soham. Stub: in-memory store with version counting. Replace with SQLAlchemy."""
from datetime import datetime, timezone
from typing import Dict, List

from app.schemas import AuditEvent, ContentUnit, Finding, SubmissionResponse

_store: Dict[str, SubmissionResponse] = {}


def save_analysis(
    *,
    submission_id: str,
    workspace_id: str,
    title: str,
    actor_id: str,
    status: str,
    current_text: str,
    units: List[ContentUnit],
    findings: List[Finding],
    audit_events: List[AuditEvent],
) -> SubmissionResponse:
    previous = _store.get(submission_id)
    version = previous.version + 1 if previous else 1
    prior_log = previous.audit_log if previous else []
    response = SubmissionResponse(
        submission_id=submission_id,
        title=title,
        status=status,  # type: ignore[arg-type]
        version=version,
        current_text=current_text,
        findings=findings,
        audit_log=prior_log + audit_events,
    )
    _store[submission_id] = response
    return response
