from datetime import date
import logging

import pytest

from app.services import verification
from app.services.orchestrator import VERIFICATION_TIMEOUT_S, _Run, _settle
from app.services.verification import (
    ContentUnit,
    EntryType,
    EvidenceItem,
    Severity,
    analyze_text_unit,
    _deterministic_mock_analyze,
)


@pytest.fixture
def make_unit():
    def build(text: str) -> ContentUnit:
        return ContentUnit(unit_id="unit-1", workspace_id="ws_test", text=text)

    return build


@pytest.fixture
def make_evidence():
    def build(
        *,
        entry_type: EntryType,
        title: str,
        quote: str,
        preferred_replacement: str | None = None,
        is_expired: bool = False,
    ) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=f"evidence-{title}",
            workspace_id="ws_test",
            entry_type=entry_type,
            category="general",
            title=title,
            quote=quote,
            preferred_replacement=preferred_replacement,
            severity=Severity.MAJOR,
            expiry_date=date(2020, 1, 1) if is_expired else None,
            is_expired=is_expired,
            similarity_score=0.9,
        )

    return build


def spans(text: str, evidence: list[EvidenceItem] | None = None) -> list[str]:
    return [
        finding.original_span
        for finding in _deterministic_mock_analyze(
            ContentUnit(unit_id="unit-1", workspace_id="ws_test", text=text),
            evidence or [],
        )
    ]


def test_flags_hash_one_with_leading_space(make_unit):
    findings = _deterministic_mock_analyze(make_unit("Our #1 product"), [])

    assert "#1" in [finding.original_span for finding in findings]


def test_flags_uptime_without_supporting_fact(make_unit):
    findings = _deterministic_mock_analyze(make_unit("We deliver 100% uptime."), [])

    assert "100% uptime" in [finding.original_span for finding in findings]


def test_negated_fact_does_not_support_uptime(make_unit, make_evidence):
    prohibition = make_evidence(
        entry_type=EntryType.FACT,
        title="Uptime prohibition",
        quote="Never claim 100% uptime or zero downtime.",
    )
    findings = _deterministic_mock_analyze(
        make_unit("We deliver 100% uptime."), [prohibition]
    )

    assert "100% uptime" in [finding.original_span for finding in findings]


def test_non_negated_fact_supports_uptime(make_unit, make_evidence):
    supporting_fact = make_evidence(
        entry_type=EntryType.FACT,
        title="Uptime SLA",
        quote="The SLA uptime is 100% uptime.",
    )
    findings = _deterministic_mock_analyze(
        make_unit("We deliver 100% uptime."), [supporting_fact]
    )

    assert "100% uptime" not in [finding.original_span for finding in findings]


def test_expired_promotion_flags_number_and_unit(make_unit, make_evidence):
    expired_promotion = make_evidence(
        entry_type=EntryType.FACT,
        title="Expired promotion",
        quote="3 months free",
        is_expired=True,
    )
    findings = _deterministic_mock_analyze(
        make_unit("Get 3 months free today."), [expired_promotion]
    )

    assert "3 months" in [finding.original_span for finding in findings]


def test_banned_term_preserves_input_casing(make_unit, make_evidence):
    banned_term = make_evidence(
        entry_type=EntryType.BANNED_TERM,
        title="guaranteed",
        quote="Do not use guaranteed claims.",
    )
    findings = _deterministic_mock_analyze(make_unit("Guaranteed results."), [banned_term])

    assert [finding.original_span for finding in findings] == ["Guaranteed"]


def test_preferred_term_flags_variant_not_preferred_form(make_unit, make_evidence):
    preferred_term = make_evidence(
        entry_type=EntryType.PREFERRED_TERM,
        title="users -> customers",
        quote="Use customers instead of users.",
        preferred_replacement="customers",
    )

    assert spans("users", [preferred_term]) == ["users"]
    assert spans("customers", [preferred_term]) == []
    assert spans("NovaTech customers", [preferred_term]) == []
    assert spans("NovaTech users", [preferred_term]) == []


def test_analyze_logs_mock_path_for_empty_text(make_unit, monkeypatch, caplog):
    class MockClient:
        is_configured = False

    monkeypatch.setattr(verification, "LLMProviderClient", MockClient)
    with caplog.at_level(logging.INFO, logger="app.services.verification"):
        assert analyze_text_unit(make_unit("  "), []) == []

    assert "Verification path for unit unit-1: mock" in caplog.text


def test_llm_failure_logs_traceback_and_uses_fallback(make_unit, monkeypatch, caplog):
    class ConfiguredClient:
        is_configured = True

    def fail_llm(*args, **kwargs):
        raise ValueError("provider unavailable")

    monkeypatch.setattr(verification, "LLMProviderClient", ConfiguredClient)
    monkeypatch.setattr(verification, "_llm_analyze", fail_llm)
    with caplog.at_level(logging.INFO, logger="app.services.verification"):
        findings = analyze_text_unit(make_unit("Our #1 product"), [])

    assert any(f.original_span == "#1" for f in findings)
    assert "Verification path for unit unit-1: llm" in caplog.text
    failure = next(record for record in caplog.records if "LLM verification failed" in record.message)
    assert failure.exc_info is not None
    assert failure.exc_info[0] is ValueError
    assert "provider unavailable" in failure.getMessage()


def test_settle_audit_contains_truncated_exception_reason():
    run = _Run(submission_id="submission-1", actor="test")
    _settle(ValueError("x" * 250), run, "llm_verification")

    assert run.audit[-1].detail == f"ValueError: {'x' * 200}"


def test_settle_audit_contains_timeout_duration():
    run = _Run(submission_id="submission-1", actor="test")
    _settle(TimeoutError(), run, "llm_verification")

    assert run.audit[-1].detail == f"timeout after {VERIFICATION_TIMEOUT_S}s"