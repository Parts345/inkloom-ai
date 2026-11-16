"""Owner: Soham. Upload API, review workflow, approval decisions, audit trail, and knowledge endpoints."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import date, datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FindingRecord, KnowledgeEntry, Submission, Workspace, new_id, utcnow
from app.routers.demo import DEMO_MODE, DEMO_USERS, DEMO_WORKSPACE_ID, Actor
from app.schemas import SubmissionResponse
from app.services import persistence as P
from app.services.orchestrator import AnalysisInputError, analyse_submission

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUBMITTING_ROLES = {"creator", "admin"}
APPROVING_ROLES = {"approver", "admin"}
ALL_ROLES = ("creator", "approver", "admin")

router = APIRouter(tags=["submissions"])


# --------------------------------------------------------------------------- Auth / RBAC
def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _decode_jwt(token: str) -> dict:
    """Verify and decode an HS256 JWT using Python's standard library (zero external deps)."""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=401, detail="Bearer auth not configured (JWT_SECRET unset)")
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Malformed JWT")
        header_b64, payload_b64, sig_b64 = parts
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            raise ValueError("Unsupported algorithm")

        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, _b64url_decode(sig_b64)):
            raise ValueError("Invalid signature")

        claims = json.loads(_b64url_decode(payload_b64))
        exp = claims.get("exp")
        if exp is not None and time.time() > float(exp):
            raise ValueError("Token expired")
        return claims
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def resolve_actor(
    x_demo_role: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Actor:
    """Supports optional Bearer JWT when provided, or seamless X-Demo-Role switching in DEMO_MODE."""
    if authorization and authorization.lower().startswith("bearer "):
        claims = _decode_jwt(authorization[7:].strip())
        role = str(claims.get("role", "")).strip().lower()
        if role not in ALL_ROLES:
            raise HTTPException(status_code=401, detail="Token contains invalid role")
        return Actor(
            user_id=str(claims.get("sub") or f"user_{role}"),
            name=str(claims.get("name") or role.capitalize()),
            role=role,
            workspace_id=str(claims.get("workspace_id") or DEMO_WORKSPACE_ID),
        )

    if not DEMO_MODE:
        raise HTTPException(status_code=401, detail="Authentication required")
    role = (x_demo_role or "creator").strip().lower()
    if role not in DEMO_USERS:
        raise HTTPException(status_code=400, detail="Unknown demo role")
    user_id, name = DEMO_USERS[role]
    return Actor(user_id=user_id, name=name, role=role, workspace_id=DEMO_WORKSPACE_ID)


# --------------------------------------------------------------------------- Request / Response DTOs
class KnowledgeIn(BaseModel):
    entry_type: Literal["guideline", "fact", "preferred_term", "banned_term"]
    category: Optional[str] = None
    title: str
    rule_text: str
    severity: Literal["critical", "major", "minor"] = "minor"
    owner: Optional[str] = None
    source_doc: Optional[str] = None
    expiry_date: Optional[date] = None


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    entry_type: str
    category: Optional[str] = None
    title: str
    rule_text: str
    severity: str
    owner: Optional[str] = None
    source_doc: Optional[str] = None
    expiry_date: Optional[date] = None
    version: int
    is_expired: bool = False


class SubmissionSummary(BaseModel):
    submission_id: str
    title: str
    status: str
    version: int
    created_by: Optional[str] = None
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    total_findings: int
    open_findings: int
    open_critical: int


class FindingUpdate(BaseModel):
    action: Literal["accept", "reject", "dispute"]
    dispute_reason: Optional[str] = None


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "request_revision"]
    comment: Optional[str] = None
    justification: Optional[str] = None


# --------------------------------------------------------------------------- Helpers
def _workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id) or db.query(Workspace).first()
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def _get_submission(db: Session, submission_id: str) -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


def _knowledge_out(entry: KnowledgeEntry) -> KnowledgeOut:
    out = KnowledgeOut.model_validate(entry)
    out.is_expired = bool(entry.expiry_date and entry.expiry_date < date.today())
    return out


def _refresh_verification_index(db: Session, workspace_id: str) -> None:
    """Notify Ushank's RAG verification service if it defines a cache/index rebuild hook."""
    try:
        from app.services import verification
    except ImportError:
        return
    for hook_name in ("rebuild_workspace_index", "invalidate_index", "reload_knowledge"):
        hook = getattr(verification, hook_name, None)
        if callable(hook):
            try:
                hook(workspace_id)
            except TypeError:
                entries = db.query(KnowledgeEntry).filter_by(workspace_id=workspace_id).all()
                hook(workspace_id, entries)
            except Exception:
                logger.exception("Verification index refresh hook '%s' failed", hook_name)
            break


# --------------------------------------------------------------------------- Submissions & Upload
@router.post("/submissions", response_model=SubmissionResponse, status_code=201)
async def create_submission(
    title: str = Form(..., min_length=1, max_length=200),
    text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    actor: Actor = Depends(resolve_actor),
) -> SubmissionResponse:
    if actor.role not in SUBMITTING_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot submit content")

    file_bytes: Optional[bytes] = None
    file_name: Optional[str] = None
    if file is not None and file.filename:
        file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
        file_name = file.filename

    try:
        return await analyse_submission(
            workspace_id=actor.workspace_id,
            actor_id=actor.user_id,
            title=title,
            text=text or None,
            file_name=file_name,
            file_bytes=file_bytes,
        )
    except AnalysisInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/submissions", response_model=List[SubmissionSummary])
def list_submissions(
    status: Optional[str] = None,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> List[SubmissionSummary]:
    q = db.query(Submission).filter_by(workspace_id=actor.workspace_id)
    if status:
        q = q.filter_by(status=status)
    out: List[SubmissionSummary] = []
    for s in q.order_by(Submission.created_at.desc()).all():
        open_f = [f for f in s.findings if f.status == "open"]
        out.append(
            SubmissionSummary(
                submission_id=s.id,
                title=s.title,
                status=s.status,
                version=s.version,
                created_by=s.created_by,
                created_at=s.created_at,
                approved_by=s.approved_by,
                approved_at=s.approved_at,
                total_findings=len(s.findings),
                open_findings=len(open_f),
                open_critical=sum(1 for f in open_f if f.severity == "critical"),
            )
        )
    return out


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
def get_submission(submission_id: str, db: Session = Depends(get_db)) -> SubmissionResponse:
    return P.submission_to_response(db, _get_submission(db, submission_id))


@router.post("/submissions/{submission_id}/submit-for-review", response_model=SubmissionResponse)
def submit_for_review(
    submission_id: str,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> SubmissionResponse:
    """Moves a 'draft' or 'revision_required' submission into 'in_review'."""
    if actor.role not in SUBMITTING_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot submit drafts for review")
    sub = _get_submission(db, submission_id)
    if sub.status not in ("draft", "revision_required"):
        raise HTTPException(
            status_code=409,
            detail=f"Submission is '{sub.status}' and cannot be submitted for review",
        )
    sub.status = "in_review"
    P.add_audit(
        db,
        sub.id,
        actor.role,
        actor.name,
        "submitted_for_review",
        f"Version {sub.version} moved to in_review",
    )
    db.commit()
    db.refresh(sub)
    return P.submission_to_response(db, sub)


# --------------------------------------------------------------------------- Findings Review
@router.patch("/findings/{finding_id}", response_model=SubmissionResponse)
def update_finding(
    finding_id: int,
    body: FindingUpdate,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> SubmissionResponse:
    if actor.role not in SUBMITTING_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot act on findings")

    rec = db.get(FindingRecord, finding_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Finding not found")

    sub = _get_submission(db, rec.submission_id)
    if sub.status in ("approved", "rejected"):
        raise HTTPException(
            status_code=409,
            detail=f"Submission is '{sub.status}' and can no longer be edited",
        )
    if rec.status == "accepted":
        raise HTTPException(status_code=409, detail="Finding was already accepted and applied")

    if body.action == "accept":
        if not rec.suggested_edit:
            raise HTTPException(
                status_code=400,
                detail="This finding has no suggested edit. Reject or dispute it instead.",
            )
        if rec.original_span not in sub.current_content:
            raise HTTPException(
                status_code=409,
                detail="The flagged text no longer appears in the current draft",
            )
        sub.current_content = sub.current_content.replace(rec.original_span, rec.suggested_edit, 1)
        sub.version += 1
        rec.status = "accepted"
        rec.dispute_reason = None
        P.add_audit(
            db,
            sub.id,
            actor.role,
            actor.name,
            "finding_accepted",
            f"Finding #{rec.id}: '{rec.original_span}' -> '{rec.suggested_edit}' (now v{sub.version})",
        )
    elif body.action == "reject":
        rec.status = "rejected"
        rec.dispute_reason = None
        P.add_audit(
            db,
            sub.id,
            actor.role,
            actor.name,
            "finding_rejected",
            f"Finding #{rec.id} ({rec.severity}): suggested edit declined for '{rec.original_span}'",
        )
    else:  # dispute
        reason = (body.dispute_reason or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="dispute_reason is required to dispute a finding")
        rec.status = "disputed"
        rec.dispute_reason = reason
        P.add_audit(
            db,
            sub.id,
            actor.role,
            actor.name,
            "finding_disputed",
            f"Finding #{rec.id} ({rec.severity}) disputed: {reason}",
        )

    db.commit()
    db.refresh(sub)
    return P.submission_to_response(db, sub)


# --------------------------------------------------------------------------- Approval Workflow
@router.post("/submissions/{submission_id}/decide", response_model=SubmissionResponse)
def decide_submission(
    submission_id: str,
    body: DecisionIn,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> SubmissionResponse:
    if actor.role not in APPROVING_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only approvers or admins can approve, reject, or request revisions",
        )

    sub = _get_submission(db, submission_id)
    if sub.status not in ("in_review", "draft"):
        raise HTTPException(
            status_code=409,
            detail=f"Submission is '{sub.status}' and cannot be decided",
        )

    comment = (body.comment or "").strip()
    if body.decision in ("reject", "request_revision") and not comment:
        raise HTTPException(
            status_code=400,
            detail="A comment is required when rejecting or requesting revisions",
        )

    if body.decision == "approve":
        blockers = [f for f in sub.findings if f.severity == "critical" and f.status == "open"]
        justification = (body.justification or "").strip()
        if blockers and not justification:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Cannot approve: critical findings are still open. Resolve or dispute them, or approve with a justification.",
                    "open_critical_finding_ids": [str(f.id) for f in blockers],
                },
            )
        sub.status = "approved"
        sub.approved_by = actor.name
        sub.approved_at = utcnow()
        action = "approved_with_override" if blockers else "approved"
        details = comment
        if blockers:
            ids = ", ".join(f"#{f.id}" for f in blockers)
            details = f"Approved with open critical findings {ids}. Justification: {justification}. {comment}".strip()
    elif body.decision == "reject":
        sub.status = "rejected"
        action = "rejected"
        details = comment
    else:
        sub.status = "revision_required"
        action = "revision_requested"
        details = comment

    P.add_audit(db, sub.id, actor.role, actor.name, action, details or None)
    db.commit()
    db.refresh(sub)
    return P.submission_to_response(db, sub)


# --------------------------------------------------------------------------- Knowledge Base
@router.get("/knowledge", response_model=List[KnowledgeOut])
def list_knowledge(
    entry_type: Optional[Literal["guideline", "fact", "preferred_term", "banned_term"]] = None,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> List[KnowledgeOut]:
    ws = _workspace(db, actor.workspace_id)
    q = db.query(KnowledgeEntry).filter_by(workspace_id=ws.id)
    if entry_type:
        q = q.filter_by(entry_type=entry_type)
    entries = q.order_by(KnowledgeEntry.entry_type, KnowledgeEntry.id).all()
    return [_knowledge_out(e) for e in entries]


@router.post("/knowledge", response_model=KnowledgeOut, status_code=201)
def add_knowledge(
    body: KnowledgeIn,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> KnowledgeOut:
    if actor.role not in APPROVING_ROLES:
        raise HTTPException(status_code=403, detail="Only approvers or admins can add knowledge entries")

    ws = _workspace(db, actor.workspace_id)
    entry = KnowledgeEntry(
        id=new_id(),
        workspace_id=ws.id,
        version=1,
        **body.model_dump(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    _refresh_verification_index(db, ws.id)
    return _knowledge_out(entry)