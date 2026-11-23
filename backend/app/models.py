"""Owner: Soham. SQLAlchemy ORM models for Inkloom AI (Round 3)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.orm import relationship

from app.database import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC timestamp (consistent across SQLite and PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(String(64), primary_key=True, default=new_id)
    name = Column(String(200), nullable=False, unique=True)
    brand_palette_hex = Column(JSON, nullable=False, default=list)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id = Column(String(64), primary_key=True, default=new_id)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    # guideline | fact | preferred_term | banned_term | asset
    entry_type = Column(String(20), nullable=False)
    category = Column(String(60), nullable=True)
    title = Column(String(300), nullable=False)
    rule_text = Column(Text, nullable=False)
    severity = Column(String(10), nullable=False, default="minor")  # critical | major | minor
    owner = Column(String(120), nullable=True)
    source_doc = Column(String(300), nullable=True)
    expiry_date = Column(Date, nullable=True)
    version = Column(Integer, nullable=False, default=1)

    # Round 3: Approved Visual Asset fields (populated when entry_type == "asset")
    asset_type = Column(String(20), nullable=True)   # logo | banner | palette
    image_path = Column(String(500), nullable=True)  # e.g. /data/approved_assets/<file>.png
    usage_rules = Column(Text, nullable=True)


class RuleRefinementProposal(Base):
    """Round 3 Imp. 3: LLM-synthesized rule rewrite generated from disputed findings."""

    __tablename__ = "rule_refinement_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    rule_id = Column(String(64), ForeignKey("knowledge_entries.id"), nullable=False, index=True)
    dispute_count = Column(Integer, nullable=False, default=0)
    cluster_summary = Column(Text, nullable=False)
    proposed_rule_text = Column(Text, nullable=False)
    # ambiguous | overly_broad | outdated | well_calibrated (nullable: proposals made before this column existed)
    diagnosis = Column(String(30), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending | applied | dismissed
    created_at = Column(DateTime, nullable=False, default=utcnow)
    applied_at = Column(DateTime, nullable=True)
    applied_by = Column(String(120), nullable=True)


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(String(64), primary_key=True, default=new_id)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    source_format = Column(String(20), nullable=False, default="text")
    original_content = Column(Text, nullable=False)
    current_content = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="draft")
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    approved_by = Column(String(120), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    findings = relationship(
        "FindingRecord",
        order_by="FindingRecord.id",
        cascade="all, delete-orphan",
    )
    audit_log = relationship(
        "AuditLog",
        order_by="AuditLog.id",
    )


class FindingRecord(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(String(64), ForeignKey("submissions.id"), nullable=False, index=True)
    unit_id = Column(String(64), nullable=False)
    category = Column(String(20), nullable=False)
    severity = Column(String(10), nullable=False)
    original_span = Column(Text, nullable=False)
    explanation = Column(Text, nullable=False)
    evidence_id = Column(String(64), nullable=True)
    evidence_title = Column(String(300), nullable=True)
    evidence_quote = Column(Text, nullable=True)
    suggested_edit = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    status = Column(String(12), nullable=False, default="open")
    dispute_reason = Column(Text, nullable=True)


class AuditLog(Base):
    """Append-only audit trail. Updates and deletes via the ORM are blocked."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(String(64), ForeignKey("submissions.id"), nullable=False, index=True)
    actor_role = Column(String(20), nullable=False)
    actor_name = Column(String(120), nullable=False)
    action = Column(String(60), nullable=False)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=utcnow)


@event.listens_for(AuditLog, "before_update")
def _audit_no_update(mapper, connection, target):
    raise PermissionError("AuditLog is append-only: updates are not allowed")


@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(mapper, connection, target):
    raise PermissionError("AuditLog is append-only: deletes are not allowed")