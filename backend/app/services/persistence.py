"""Owner: Soham. SQLAlchemy persistence for analysis results, visual assets, findings, and audit log."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AuditLog, FindingRecord, KnowledgeEntry, Submission, utcnow
from app.routers.demo import DEMO_USERS
from app.schemas import AuditEvent, ContentUnit, EvidenceItem, Finding, SubmissionResponse

_SOURCE_TYPE = {
    "guideline": "guideline",
    "fact": "fact",
    "preferred_term": "term",
    "banned_term": "term",
    "asset": "asset",
}

_ACTOR_ID_TO_ROLE_NAME = {
    user_id: (role, name) for role, (user_id, name) in DEMO_USERS.items()
}


def get_assets_dir() -> Path:
    """Resolve /data/approved_assets when /data is writable, otherwise local ./data/approved_assets."""
    env_dir = os.getenv("APPROVED_ASSETS_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    preferred = Path("/data/approved_assets")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        fallback = Path("./data/approved_assets").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def asset_public_url(image_path: Optional[str]) -> Optional[str]:
    if not image_path:
        return None
    return f"/assets/approved/{Path(image_path).name}"


def _naive_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return utcnow()
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _resolve_actor_fields(actor: str) -> tuple[str, str]:
    if actor in _ACTOR_ID_TO_ROLE_NAME:
        return _ACTOR_ID_TO_ROLE_NAME[actor]
    if actor.endswith(")") and " (" in actor:
        name, role_part = actor.rsplit(" (", 1)
        role = role_part[:-1].strip().lower()
        if role in ("creator", "approver", "admin", "system"):
            return role, name.strip()
    return "creator", actor


def add_audit(
    db: Session,
    submission_id: str,
    actor_role: str,
    actor_name: str,
    action: str,
    details: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> AuditLog:
    """Append a single immutable audit row. Caller commits."""
    row = AuditLog(
        submission_id=submission_id,
        actor_role=actor_role,
        actor_name=actor_name,
        action=action,
        details=details,
        timestamp=_naive_utc(timestamp),
    )
    db.add(row)
    return row


def save_findings(db: Session, submission_id: str, findings: List[Finding]) -> List[FindingRecord]:
    """Persist schema Finding objects (text + VLM visual) as FindingRecord rows. Caller commits."""
    rows: List[FindingRecord] = []
    for f in findings:
        ev = f.evidence
        explanation = f.explanation
        if f.visual_notes and f.visual_notes not in explanation:
            explanation = f"{explanation} [VLM Note: {f.visual_notes}]"
        row = FindingRecord(
            submission_id=submission_id,
            unit_id=f.unit_id,
            category=f.category,
            severity=f.severity,
            original_span=f.original_span,
            explanation=explanation,
            evidence_id=ev.evidence_id if ev else None,
            evidence_title=ev.title if ev else None,
            evidence_quote=ev.content if ev else None,
            suggested_edit=f.suggested_edit,
            confidence=f.confidence,
            status=f.status,
            dispute_reason=f.dispute_reason,
        )
        db.add(row)
        rows.append(row)
    return rows


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
    source_format: str = "text",
) -> SubmissionResponse:
    """Called by orchestrator.analyse_submission() to persist a submission and its findings."""
    actor_role, actor_name = _resolve_actor_fields(actor_id)
    display_content = current_text.strip() or f"[Visual Submission: {title.strip()}]"

    db = SessionLocal()
    try:
        sub = db.get(Submission, submission_id)
        if sub is None:
            sub = Submission(
                id=submission_id,
                workspace_id=workspace_id,
                title=title.strip(),
                source_format=source_format,
                original_content=display_content,
                current_content=display_content,
                version=1,
                status=status,
                created_by=actor_name,
            )
            db.add(sub)
            db.flush()
            add_audit(
                db,
                submission_id=sub.id,
                actor_role=actor_role,
                actor_name=actor_name,
                action="submission_created",
                details=f"Title '{sub.title}' ({source_format}), {len(units)} content unit(s)",
            )
        else:
            sub.title = title.strip()
            sub.current_content = display_content
            sub.version += 1
            sub.status = status
            sub.findings.clear()
            db.flush()

        for ev in audit_events:
            ev_role, ev_name = _resolve_actor_fields(ev.actor)
            add_audit(
                db,
                submission_id=sub.id,
                actor_role=ev_role,
                actor_name=ev_name,
                action=ev.action,
                details=ev.detail,
                timestamp=ev.timestamp,
            )

        save_findings(db, sub.id, findings)

        critical_count = sum(1 for f in findings if f.severity == "critical")
        add_audit(
            db,
            submission_id=sub.id,
            actor_role="system",
            actor_name="Analysis Engine",
            action="analysis_completed",
            details=f"{len(findings)} findings ({critical_count} critical); status='{status}'",
        )

        db.commit()
        db.refresh(sub)
        return submission_to_response(db, sub)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finding_to_schema(db: Session, rec: FindingRecord) -> Finding:
    evidence = None
    if rec.evidence_id:
        entry = db.get(KnowledgeEntry, rec.evidence_id)
        evidence = EvidenceItem(
            evidence_id=rec.evidence_id,
            source_type=_SOURCE_TYPE.get(entry.entry_type, "guideline") if entry else "guideline",
            title=rec.evidence_title or (entry.title if entry else rec.evidence_id),
            content=rec.evidence_quote or (entry.rule_text if entry else ""),
            owner=entry.owner if entry else None,
            expiry_date=entry.expiry_date if entry else None,
            image_url=asset_public_url(entry.image_path) if entry else None,
        )
    return Finding(
        finding_id=str(rec.id),
        unit_id=rec.unit_id,
        category=rec.category,
        severity=rec.severity,
        original_span=rec.original_span,
        explanation=rec.explanation,
        evidence=evidence,
        suggested_edit=rec.suggested_edit,
        confidence=rec.confidence,
        status=rec.status,
        dispute_reason=rec.dispute_reason,
    )


def audit_to_schema(row: AuditLog) -> AuditEvent:
    return AuditEvent(
        event_id=str(row.id),
        submission_id=row.submission_id,
        timestamp=row.timestamp,
        actor=f"{row.actor_name} ({row.actor_role})",
        action=row.action,
        detail=row.details,
    )


def submission_to_response(db: Session, sub: Submission) -> SubmissionResponse:
    return SubmissionResponse(
        submission_id=sub.id,
        title=sub.title,
        status=sub.status,  # type: ignore[arg-type]
        version=sub.version,
        current_text=sub.current_content,
        findings=[finding_to_schema(db, r) for r in sub.findings],
        audit_log=[audit_to_schema(a) for a in sub.audit_log],
    )