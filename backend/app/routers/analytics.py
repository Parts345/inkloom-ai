"""Owner: Soham. Governance analytics: disputed-rule clustering & LLM refinement synthesis."""
from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    FindingRecord,
    KnowledgeEntry,
    RuleRefinementProposal,
    Submission,
    Workspace,
)
from app.routers.demo import Actor
from app.routers.submissions import resolve_actor
from app.schemas import DisputedRuleCluster, DisputeSample

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


def _synthesize_fallback_refinement(
    rule: KnowledgeEntry,
    disputes: List[DisputeSample],
) -> tuple[str, str]:
    """Deterministic fallback until Ushank's dispute_refiner.py is merged."""
    reasons = [d.dispute_reason.strip() for d in disputes if d.dispute_reason.strip()]
    unique_reasons = list(dict.fromkeys(reasons))
    joined_reasons = "; ".join(f'"{r}"' for r in unique_reasons[:3])
    summary = (
        f"{len(disputes)} creator dispute(s) logged against '{rule.title}'. "
        f"Recurring context: {joined_reasons}."
    )
    proposed = (
        f"{rule.rule_text.rstrip('.')} "
        f"(Exception: permitted when explicitly documented in context, e.g., {unique_reasons[0]})."
        if unique_reasons
        else rule.rule_text
    )
    return summary, proposed


async def _generate_refinement_via_service(
    rule: KnowledgeEntry,
    disputes: List[DisputeSample],
) -> tuple[str, str]:
    """Call Ushank's dispute_refiner service if available; otherwise use fallback."""
    try:
        from app.services import dispute_refiner
    except ImportError:
        return _synthesize_fallback_refinement(rule, disputes)

    for fn_name in ("refine_disputed_rule", "synthesize_refinement", "cluster_and_refine"):
        fn = getattr(dispute_refiner, fn_name, None)
        if callable(fn):
            payload = [d.model_dump() for d in disputes]
            try:
                res = fn(
                    rule_id=rule.id,
                    rule_title=rule.title,
                    rule_text=rule.rule_text,
                    disputes=payload,
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, dict):
                    summary = res.get("cluster_summary") or res.get("summary") or ""
                    proposed = res.get("proposed_rule_text") or res.get("refined_rule") or ""
                else:
                    summary = getattr(res, "cluster_summary", "") or getattr(res, "summary", "")
                    proposed = getattr(res, "proposed_rule_text", "") or getattr(res, "refined_rule", "")
                if summary and proposed:
                    return str(summary), str(proposed)
            except Exception:
                logger.exception("dispute_refiner.%s failed; falling back", fn_name)
            break

    return _synthesize_fallback_refinement(rule, disputes)


@router.get("/disputed-rules", response_model=List[DisputedRuleCluster])
async def get_disputed_rules(
    refresh: bool = False,
    actor: Actor = Depends(resolve_actor),
    db: Session = Depends(get_db),
) -> List[DisputedRuleCluster]:
    """Group disputed findings by rule_id and attach LLM-generated rule refinement proposals."""
    ws = db.get(Workspace, actor.workspace_id) or db.query(Workspace).first()
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    rows = (
        db.query(FindingRecord, Submission)
        .join(Submission, FindingRecord.submission_id == Submission.id)
        .filter(
            Submission.workspace_id == ws.id,
            FindingRecord.status == "disputed",
            FindingRecord.evidence_id.isnot(None),
        )
        .order_by(FindingRecord.id.asc())
        .all()
    )

    grouped: Dict[str, List[DisputeSample]] = defaultdict(list)
    for finding, sub in rows:
        if not finding.evidence_id:
            continue
        grouped[finding.evidence_id].append(
            DisputeSample(
                finding_id=str(finding.id),
                submission_id=sub.id,
                submission_title=sub.title,
                original_span=finding.original_span,
                dispute_reason=finding.dispute_reason or "Disputed without detailed note",
            )
        )

    clusters: List[DisputedRuleCluster] = []
    dirty = False

    for rule_id, disputes in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        rule = db.get(KnowledgeEntry, rule_id)
        if rule is None:
            continue

        proposal = (
            db.query(RuleRefinementProposal)
            .filter_by(workspace_id=ws.id, rule_id=rule_id, status="pending")
            .order_by(RuleRefinementProposal.id.desc())
            .first()
        )

        if proposal is None or refresh or proposal.dispute_count != len(disputes):
            summary, proposed_text = await _generate_refinement_via_service(rule, disputes)
            if proposal is None:
                proposal = RuleRefinementProposal(
                    workspace_id=ws.id,
                    rule_id=rule_id,
                    dispute_count=len(disputes),
                    cluster_summary=summary,
                    proposed_rule_text=proposed_text,
                    status="pending",
                )
                db.add(proposal)
            else:
                proposal.dispute_count = len(disputes)
                proposal.cluster_summary = summary
                proposal.proposed_rule_text = proposed_text
            dirty = True

        clusters.append(
            DisputedRuleCluster(
                rule_id=rule.id,
                rule_title=rule.title,
                entry_type=rule.entry_type,
                category=rule.category,
                severity=rule.severity,
                current_rule_text=rule.rule_text,
                rule_version=rule.version,
                dispute_count=len(disputes),
                disputes=disputes,
                proposal_id=proposal.id,
                cluster_summary=proposal.cluster_summary,
                proposed_rule_text=proposal.proposed_rule_text,
                proposal_status=proposal.status,  # type: ignore[arg-type]
            )
        )

    if dirty:
        db.commit()
        # Populate newly assigned autoincrement proposal IDs
        for c in clusters:
            if c.proposal_id is None:
                latest = (
                    db.query(RuleRefinementProposal)
                    .filter_by(workspace_id=ws.id, rule_id=c.rule_id, status="pending")
                    .order_by(RuleRefinementProposal.id.desc())
                    .first()
                )
                if latest:
                    c.proposal_id = latest.id

    return clusters