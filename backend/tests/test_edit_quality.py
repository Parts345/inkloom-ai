from app.schemas import Finding as SharedFinding
from app.services.orchestrator import merge_findings
from app.services.verification import (
    ContentUnit,
    EntryType,
    EvidenceItem,
    FactVerificationStatus,
    Finding,
    IssueCategory,
    Severity,
    _deterministic_mock_analyze,
    _validate_and_ground,
)


def _unit(text: str) -> ContentUnit:
    return ContentUnit(unit_id="unit-1", workspace_id="ws_test", text=text)


def _finding(**overrides) -> Finding:
    values = dict(
        category=IssueCategory.TERMINOLOGY,
        fact_status=FactVerificationStatus.NOT_APPLICABLE,
        severity=Severity.MAJOR,
        original_span="users",
        explanation="Use customers instead.",
        confidence=0.95,
    )
    values.update(overrides)
    return Finding(**values)


def test_edit_equal_to_original_span_is_blanked():
    finding = _finding(original_span="users", suggested_edit="users")

    result = _validate_and_ground([finding], _unit("Our users love it."), [])

    assert result[0].suggested_edit == ""


def test_case_only_edit_is_kept():
    finding = _finding(original_span="users", suggested_edit="Users")

    result = _validate_and_ground([finding], _unit("Our users love it."), [])

    assert result[0].suggested_edit == "Users"


def test_edit_reintroducing_guaranteed_is_blanked_and_explained():
    finding = _finding(
        category=IssueCategory.FACT,
        original_span="100% uptime",
        suggested_edit="guaranteed 100% uptime",
        explanation="No SLA supports this.",
    )

    result = _validate_and_ground([finding], _unit("We offer 100% uptime."), [])

    assert result[0].suggested_edit == ""
    assert result[0].explanation.endswith("Remove the banned term instead of replacing it.")


def test_banned_term_without_replacement_is_removed_not_replaced():
    banned = EvidenceItem(
        evidence_id="kb-t03",
        workspace_id="ws_test",
        entry_type=EntryType.BANNED_TERM,
        category="terminology",
        title="guaranteed",
        quote="Do not use guaranteed claims.",
        severity=Severity.CRITICAL,
        similarity_score=0.9,
    )

    findings = _deterministic_mock_analyze(_unit("This is guaranteed."), [banned])

    assert len(findings) == 1
    assert findings[0].suggested_edit == ""
    assert "Remove the banned term." in findings[0].explanation


def _shared(finding_id: str, category: str, severity: str, span: str = "100% uptime") -> SharedFinding:
    return SharedFinding(
        finding_id=finding_id,
        unit_id="unit-1",
        category=category,
        severity=severity,
        original_span=span,
        explanation=f"{finding_id} explanation.",
        confidence=0.9,
    )


def test_same_span_across_categories_keeps_most_severe():
    fact = _shared("f1", "fact", "critical")
    terminology = _shared("f2", "terminology", "major")

    merged = merge_findings([terminology, fact])

    assert len(merged) == 1
    assert merged[0].severity == "critical"
    assert merged[0].category == "fact"


def test_different_overlapping_spans_are_both_kept():
    uptime = _shared("f1", "fact", "critical", span="100% uptime")
    downtime = _shared("f2", "fact", "critical", span="zero downtime")

    merged = merge_findings([uptime, downtime])

    assert sorted(f.original_span for f in merged) == ["100% uptime", "zero downtime"]
