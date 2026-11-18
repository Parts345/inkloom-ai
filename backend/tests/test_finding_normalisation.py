from app.services.verification import (
    FactVerificationStatus,
    Finding,
    IssueCategory,
    LLMAnalysisResponse,
    Severity,
    _parse_llm_payload,
)


def _base(**overrides) -> dict:
    values = dict(
        category=IssueCategory.TERMINOLOGY,
        severity=Severity.MAJOR,
        original_span="users",
        explanation="Use customers.",
        confidence=0.95,
    )
    values.update(overrides)
    return values


def test_null_fact_status_becomes_not_applicable():
    finding = Finding(**_base(fact_status=None))

    assert finding.fact_status == FactVerificationStatus.NOT_APPLICABLE


def test_null_suggested_edit_becomes_empty_string():
    finding = Finding(**_base(suggested_edit=None))

    assert finding.suggested_edit == ""


def test_null_explanation_becomes_empty_string():
    finding = Finding(**_base(explanation=None))

    assert finding.explanation == ""


def test_parse_llm_payload_accepts_nulls_and_returns_one_finding():
    raw = (
        '{"findings": [{"category": "terminology", "fact_status": null, '
        '"severity": "major", "original_span": "users", "evidence_id": null, '
        '"evidence_quote": null, "explanation": "Use customers.", '
        '"suggested_edit": null, "confidence": 0.95}]}'
    )

    parsed = _parse_llm_payload(raw)

    assert isinstance(parsed, LLMAnalysisResponse)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].fact_status == FactVerificationStatus.NOT_APPLICABLE
    assert parsed.findings[0].suggested_edit == ""
