from app.services import verification
from app.schemas import Finding as SharedFinding
from app.services.orchestrator import merge_findings
from app.services.verification import (
    ContentUnit,
    Finding,
    IssueCategory,
    Severity,
    _validate_and_ground,
    analyze_text_unit,
    _clean_suggested_edit,
)


class ConfiguredClient:
    is_configured = True


def make_unit(text: str) -> ContentUnit:
    return ContentUnit(unit_id="unit-1", workspace_id="ws_test", text=text)


def make_finding(original_span: str, suggested_edit: str = "replacement") -> Finding:
    return Finding(
        category=IssueCategory.FACT,
        severity=Severity.MAJOR,
        original_span=original_span,
        explanation="Unsupported claim.",
        suggested_edit=suggested_edit,
        confidence=0.9,
    )


def test_llm_finding_covers_matching_mock_span(monkeypatch):
    llm_finding = make_finding("100% uptime guaranteed and zero downtime")
    monkeypatch.setattr(verification, "LLMProviderClient", ConfiguredClient)
    monkeypatch.setattr(verification, "_llm_analyze", lambda *args, **kwargs: [llm_finding])

    findings = analyze_text_unit(make_unit("100% uptime guaranteed and zero downtime"), [])

    assert findings == [llm_finding]


def test_llm_success_keeps_uncovered_hash_one_mock_finding(monkeypatch):
    monkeypatch.setattr(verification, "LLMProviderClient", ConfiguredClient)
    monkeypatch.setattr(verification, "_llm_analyze", lambda *args, **kwargs: [])

    findings = analyze_text_unit(make_unit("Our #1 product"), [])

    assert any(finding.original_span == "#1" for finding in findings)


def test_clean_suggested_edit_blanks_markdown_and_preserves_explanation():
    finding = make_finding("team photo", "![Team photo](team-photo.jpg)")
    explanation = finding.explanation

    _clean_suggested_edit(finding, [])

    assert finding.suggested_edit == ""
    assert finding.explanation == explanation


def test_clean_suggested_edit_blanks_urls_and_filenames():
    for suggestion in ("See https://example.com", "Use team-photo.jpg"):
        finding = make_finding("team photo", suggestion)
        explanation = finding.explanation

        _clean_suggested_edit(finding, [])

        assert finding.suggested_edit == ""
        assert finding.explanation == explanation


def test_clean_suggested_edit_keeps_case_only_change():
    finding = make_finding("Q4 Enterprise Launch Announcement", "Q4 enterprise launch announcement")

    _clean_suggested_edit(finding, [])

    assert finding.suggested_edit == "Q4 enterprise launch announcement"


def test_validate_and_ground_expands_bare_exclamation_mark():
    finding = make_finding("!", "")

    findings = _validate_and_ground([finding], make_unit("Our service has zero downtime!"), [])

    assert findings[0].original_span == "zero downtime!"
    assert findings[0].suggested_edit == "zero downtime"


def test_validate_and_ground_claims_distinct_exclamation_occurrences():
    findings = [make_finding("!"), make_finding("!")]

    grounded = _validate_and_ground(
        findings,
        make_unit("It is fast! It has zero downtime!"),
        [],
    )

    assert [finding.original_span for finding in grounded] == ["It is fast!", "zero downtime!"]


def test_merge_findings_keeps_case_only_suggestion():
    finding = SharedFinding(
        finding_id="finding-1",
        unit_id="unit-1",
        category="terminology",
        severity="major",
        original_span="users",
        explanation="Use the preferred capitalization.",
        suggested_edit="Users",
        confidence=0.9,
    )

    merged = merge_findings([finding])

    assert merged[0].suggested_edit == "Users"