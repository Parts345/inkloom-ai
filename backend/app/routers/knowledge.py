"""Owner: Soham. Knowledge Base, Approved Brand Assets upload, and Rule Refinement application."""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    KnowledgeEntry,
    RuleRefinementProposal,
    Submission,
    Workspace,
    new_id,
    utcnow,
)
from app.routers.demo import Actor
from app.routers.submissions import resolve_actor
from app.schemas import ApplyRefinementIn, ApplyRefinementOut, ApprovedAssetOut, AssetType
from app.services import persistence as P

logger = logging.getLogger(__name__)

MAX_ASSET_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
ASSET_UPLOAD_ROLES = {"approver", "admin"}
REFINEMENT_APPLY_ROLES = {"admin"}

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


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
    asset_type: Optional[str] = None
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    usage_rules: Optional[str] = None


def _workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id) or db.query(Workspace).first()
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def _knowledge_out(entry: KnowledgeEntry) -> KnowledgeOut:
    out = KnowledgeOut.model_validate(entry)
    out.is_expired = bool(entry.expiry_date and entry.expiry_date < date.today())
    out.image_url = P.asset_public_url(entry.image_path)
    return out


def _asset_out(entry: KnowledgeEntry) -> ApprovedAssetOut:
    return ApprovedAssetOut(
        id=entry.id,
        workspace_id=entry.workspace_id,
        title=entry.title,
        asset_type=entry.asset_type or "logo",
        usage_rules=entry.usage_rules or entry.rule_text,
        image_path=entry.image_path or "",
        image_url=P.asset_public_url(entry.image_path) or "",
        owner=entry.owner,
        version=entry.version,
    )


def reindex_knowledge_base(db: Session, workspace_id: str) -> bool:
    """Re-index Ushank's FAISS vector store whenever rules or assets change."""
    try:
        from app.services import verification
    except ImportError:
        return False

    entries = db.query(KnowledgeEntry).filter_by(workspace_id=workspace_id).all()
    for hook_name in (
        "rebuild_workspace_index",
        "reindex_workspace",
        "build_index",
        "reload_knowledge",
        "invalidate_index",
    ):
        hook = getattr(verification, hook_name, None)
        if callable(hook):
            try:
                hook(workspace_id, entries)
                return True
            except TypeError:
                try:
                    hook(workspace_id)
                    return True
                except TypeError:
                    hook()
                    return True
            except Exception:
                logger.exception("FAISS re-index hook '%s' failed", hook_name)
                return False
    return False


# --------------------------------------------------------------------------- Knowledge Rules & Facts
@router.get("", response_model=List[KnowledgeOut])
def list_knowledge(
    entry_type: Optional[Literal["guideline", "fact", "preferred_term", "banned_term", "asset"]] = None,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> List[KnowledgeOut]:
    ws = _workspace(db, actor.workspace_id)
    q = db.query(KnowledgeEntry).filter_by(workspace_id=ws.id)
    if entry_type:
        q = q.filter_by(entry_type=entry_type)
    entries = q.order_by(KnowledgeEntry.entry_type, KnowledgeEntry.id).all()
    return [_knowledge_out(e) for e in entries]


@router.post("", response_model=KnowledgeOut, status_code=201)
def add_knowledge(
    body: KnowledgeIn,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> KnowledgeOut:
    if actor.role not in ASSET_UPLOAD_ROLES:
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
    reindex_knowledge_base(db, ws.id)
    return _knowledge_out(entry)


# --------------------------------------------------------------------------- Approved Visual Assets
@router.get("/assets", response_model=List[ApprovedAssetOut])
def list_approved_assets(
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> List[ApprovedAssetOut]:
    ws = _workspace(db, actor.workspace_id)
    entries = (
        db.query(KnowledgeEntry)
        .filter_by(workspace_id=ws.id, entry_type="asset")
        .order_by(KnowledgeEntry.id.desc())
        .all()
    )
    return [_asset_out(e) for e in entries]


@router.post("/assets/upload", response_model=ApprovedAssetOut, status_code=201)
async def upload_approved_asset(
    title: str = Form(..., min_length=1, max_length=200),
    asset_type: AssetType = Form(...),
    usage_rules: str = Form(..., min_length=1),
    file: UploadFile = File(...),
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> ApprovedAssetOut:
    """Admin/Approver: upload an approved reference PNG/JPG asset to /data/approved_assets."""
    if actor.role not in ASSET_UPLOAD_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only approvers or admins can upload approved brand assets",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Asset filename is required")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=415, detail="Only PNG and JPG images are supported")

    data = await file.read(MAX_ASSET_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="Asset exceeds the 10 MB limit")

    ws = _workspace(db, actor.workspace_id)
    asset_id = f"asset-{new_id()[:12]}"
    safe_filename = f"{asset_id}{ext}"
    dest_path = P.get_assets_dir() / safe_filename
    dest_path.write_bytes(data)

    entry = KnowledgeEntry(
        id=asset_id,
        workspace_id=ws.id,
        entry_type="asset",
        category="visual",
        title=title.strip(),
        rule_text=usage_rules.strip(),
        severity="major",
        owner=actor.name,
        source_doc=f"Approved Asset ({asset_type})",
        version=1,
        asset_type=asset_type,
        image_path=str(dest_path),
        usage_rules=usage_rules.strip(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    reindex_knowledge_base(db, ws.id)
    return _asset_out(entry)


# --------------------------------------------------------------------------- One-Click Rule Refinement
@router.post("/rules/{rule_id}/apply-refinement", response_model=ApplyRefinementOut)
def apply_rule_refinement(
    rule_id: str,
    body: Optional[ApplyRefinementIn] = None,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> ApplyRefinementOut:
    """Admin only: apply an LLM-suggested rule rewrite, re-index FAISS, and log to AuditLog."""
    if actor.role not in REFINEMENT_APPLY_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only admins can apply rule refinements",
        )

    ws = _workspace(db, actor.workspace_id)
    rule = db.get(KnowledgeEntry, rule_id)
    if rule is None or rule.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="Rule not found in workspace")

    proposal = (
        db.query(RuleRefinementProposal)
        .filter_by(workspace_id=ws.id, rule_id=rule_id, status="pending")
        .order_by(RuleRefinementProposal.id.desc())
        .first()
    )

    override_text = (body.proposed_rule_text or "").strip() if body else ""
    new_rule_text = override_text or (proposal.proposed_rule_text.strip() if proposal else "")
    if not new_rule_text:
        raise HTTPException(
            status_code=400,
            detail="No pending refinement proposal found and no proposed_rule_text provided",
        )

    old_rule_text = rule.rule_text
    rule.rule_text = new_rule_text
    if rule.entry_type == "asset":
        rule.usage_rules = new_rule_text
    rule.version += 1

    if proposal is not None:
        proposal.proposed_rule_text = new_rule_text
        proposal.status = "applied"
        proposal.applied_at = utcnow()
        proposal.applied_by = actor.name

    # Append-only AuditLog requires a submission_id FK: attach to the most recent submission in workspace
    latest_sub = (
        db.query(Submission)
        .filter_by(workspace_id=ws.id)
        .order_by(Submission.created_at.desc())
        .first()
    )
    if latest_sub is not None:
        P.add_audit(
            db,
            submission_id=latest_sub.id,
            actor_role=actor.role,
            actor_name=actor.name,
            action="rule_refined_from_disputes",
            details=(
                f"Rule '{rule.id}' ({rule.title}) updated to v{rule.version}: "
                f"'{old_rule_text}' -> '{new_rule_text}'"
            ),
        )

    db.commit()
    reindexed = reindex_knowledge_base(db, ws.id)

    return ApplyRefinementOut(
        rule_id=rule.id,
        title=rule.title,
        old_rule_text=old_rule_text,
        new_rule_text=new_rule_text,
        new_version=rule.version,
        proposal_id=proposal.id if proposal else None,
        reindexed=reindexed,
    )