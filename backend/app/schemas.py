"""Shared data contracts. Every teammate builds against these models.

Changing a field here is a team-wide change: announce it before merging.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

UnitType = Literal["text", "image"]
SourceType = Literal["guideline", "fact", "term", "asset"]
FindingCategory = Literal["fact", "tone", "terminology", "visual", "accessibility"]
Severity = Literal["critical", "major", "minor"]
FindingStatus = Literal["open", "accepted", "rejected", "disputed"]
SubmissionStatus = Literal["draft", "in_review", "revision_required", "approved", "rejected"]


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

    @model_validator(mode="after")
    def _derive_expiry(self) -> "EvidenceItem":
        # Always recomputed, so a stale flag from storage can never mark an expired entry valid.
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
