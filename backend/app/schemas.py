"""Owner: Soham. Shared data contracts across backend services and frontend API."""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

UnitType = Literal["text", "image"]
SourceType = Literal["guideline", "fact", "term", "asset"]
AssetType = Literal["logo", "banner", "palette"]
FindingCategory = Literal["fact", "tone", "terminology", "visual", "accessibility"]
Severity = Literal["critical", "major", "minor"]
FindingStatus = Literal["open", "accepted", "rejected", "disputed"]
SubmissionStatus = Literal["draft", "in_review", "revision_required", "approved", "rejected"]
ProposalStatus = Literal["pending", "applied", "dismissed"]


class ColorPair(BaseModel):
    foreground: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class Dimensions(BaseModel):
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class UnitMetadata(BaseModel):
    page: Optional[int] = Field(default=None, ge=1)
    heading_level: Optional[int] = Field(default=None, ge=1, le=6)
    color_pairs: List[ColorPair] = Field(default_factory=list)
    alt_text: Optional[str] = None
    dimensions: Optional[Dimensions] = None
    # Round 3 Imp. 1: OCR extraction metadata
    is_ocr: bool = False
    ocr_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Round 3 Imp. 2: Image path / MIME metadata for VLM verification
    image_path: Optional[str] = None
    mime_type: Optional[str] = None


class ContentUnit(BaseModel):
    unit_id: str
    unit_type: UnitType
    content: str = Field(
        description="Text body for text units; storage path or description for image units"
    )
    metadata: UnitMetadata = Field(default_factory=UnitMetadata)


class EvidenceItem(BaseModel):
    evidence_id: str
    source_type: SourceType
    title: str
    content: str
    owner: Optional[str] = None
    expiry_date: Optional[date] = None
    is_expired: bool = False
    # Round 3 Imp. 2: Optional preview URL when evidence is an approved visual asset
    image_url: Optional[str] = None

    @model_validator(mode="after")
    def _derive_expiry(self) -> "EvidenceItem":
        self.is_expired = self.expiry_date is not None and self.expiry_date < date.today()
        return self


class Finding(BaseModel):
    finding_id: str
    unit_id: str
    category: FindingCategory
    severity: Severity
    original_span: str
    explanation: str
    evidence: Optional[EvidenceItem] = None
    suggested_edit: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    status: FindingStatus = "open"
    dispute_reason: Optional[str] = None
    # Round 3 Imp. 2: Optional VLM visual notes
    visual_notes: Optional[str] = None

    @model_validator(mode="after")
    def _dispute_requires_reason(self) -> "Finding":
        if self.status == "disputed" and not self.dispute_reason:
            raise ValueError("A disputed finding requires dispute_reason")
        return self


class AuditEvent(BaseModel):
    event_id: str
    submission_id: str
    timestamp: datetime
    actor: str
    action: str
    detail: Optional[str] = None


class SubmissionResponse(BaseModel):
    submission_id: str
    title: str
    status: SubmissionStatus
    version: int = Field(ge=1)
    current_text: str
    findings: List[Finding] = Field(default_factory=list)
    audit_log: List[AuditEvent] = Field(default_factory=list)


# --------------------------------------------------------------------------- Round 3 Schemas
class ApprovedAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    title: str
    asset_type: str
    usage_rules: str
    image_path: str
    image_url: str
    owner: Optional[str] = None
    version: int = 1


class DisputeSample(BaseModel):
    finding_id: str
    submission_id: str
    submission_title: str
    original_span: str
    dispute_reason: str


class DisputedRuleCluster(BaseModel):
    rule_id: str
    rule_title: str
    entry_type: str
    category: Optional[str] = None
    severity: str
    current_rule_text: str
    rule_version: int
    dispute_count: int
    disputes: List[DisputeSample] = Field(default_factory=list)
    proposal_id: Optional[int] = None
    cluster_summary: str
    proposed_rule_text: str
    proposal_status: ProposalStatus = "pending"


class ApplyRefinementIn(BaseModel):
    proposed_rule_text: Optional[str] = None


class ApplyRefinementOut(BaseModel):
    rule_id: str
    title: str
    old_rule_text: str
    new_rule_text: str
    new_version: int
    proposal_id: Optional[int] = None
    reindexed: bool