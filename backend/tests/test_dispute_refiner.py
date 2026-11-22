"""
Unit tests for Improvement 3: Dispute-Driven Rule Refinement (`dispute_refiner.py`).
Covers mocked LLM structured JSON synthesis and deterministic offline fallback behavior.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.dispute_refiner import (
    CreatorDisputeRecord,
    DisputeRefiner,
    RuleEntryInput,
)


def _mock_llm_client(payload: dict) -> MagicMock:
    mock_client = MagicMock()
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    mock_client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
    return mock_client


@pytest.fixture()
def sample_rule() -> RuleEntryInput:
    return RuleEntryInput(
        rule_id="rule-ai-claims-01",
        title="No Unverified AI Autonomy Claims",
        description="Marketing copy must not state that NovaTech AI operates without human oversight.",
        category="compliance",
        severity="critical",
    )


@pytest.fixture()
def sample_disputes() -> list[CreatorDisputeRecord]:
    return [
        CreatorDisputeRecord(
            dispute_reason="This was an educational diagram explaining automated alert triage with human approval.",
            flagged_text="Automated 24/7 alert triage reduces manual toil",
            submission_title="SOC Analyst Playbook Blog",
        ),
        CreatorDisputeRecord(
            dispute_reason="False positive: we explicitly stated human-in-the-loop sign-off in the next sentence.",
            flagged_text="autonomous incident grouping",
            submission_title="Q3 Release Notes",
        ),
        CreatorDisputeRecord(
            dispute_reason="Context is an internal comparison chart, not an autonomous execution claim.",
            flagged_text="automated policy checks",
            submission_title="Enterprise Architecture Brief",
        ),
    ]


def test_refine_rule_with_mocked_llm(
    sample_rule: RuleEntryInput, sample_disputes: list[CreatorDisputeRecord]
) -> None:
    mock_payload = {
        "cluster_summary": (
            "Creators are repeatedly flagged when describing automated triage or policy checks "
            "even when human-in-the-loop approval is explicitly disclosed."
        ),
        "diagnosis": "overly_broad",
        "proposed_rule_text": (
            "Marketing copy must not claim end-to-end autonomous execution without human approval. "
            "Describing automated triage, grouping, or pre-checks is permitted when human-in-the-loop "
            "oversight is explicitly stated."
        ),
        "proposed_severity": "major",
        "rationale": (
            "Distinguishes prohibited full-autonomy promises from accurate descriptions of assisted automation."
        ),
    }
    mock_client = _mock_llm_client(mock_payload)

    refiner = DisputeRefiner(
        api_key="test-key",
        model="llama-3.3-70b-versatile",
        client=mock_client,
    )
    proposal = refiner.refine_rule(sample_rule, sample_disputes)

    assert proposal.rule_id == "rule-ai-claims-01"
    assert proposal.diagnosis == "overly_broad"
    assert proposal.proposed_severity == "major"
    assert proposal.dispute_count == 3
    assert proposal.used_fallback is False
    assert proposal.model_used == "llama-3.3-70b-versatile"
    assert "human-in-the-loop" in proposal.proposed_rule_text


def test_refine_rule_offline_deterministic_fallback(
    sample_rule: RuleEntryInput,
    sample_disputes: list[CreatorDisputeRecord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    refiner = DisputeRefiner(api_key=None)
    proposal = refiner.refine_rule(sample_rule, sample_disputes)

    assert proposal.rule_id == sample_rule.rule_id
    assert proposal.used_fallback is True
    assert proposal.model_used == "deterministic-heuristic"
    assert proposal.diagnosis == "overly_broad"
    assert proposal.dispute_count == 3
    assert "3 creator dispute(s)" in proposal.cluster_summary
    assert len(proposal.proposed_rule_text) > len(sample_rule.description)


def test_refine_rule_zero_disputes(sample_rule: RuleEntryInput) -> None:
    refiner = DisputeRefiner(api_key="test-key")
    proposal = refiner.refine_rule(sample_rule, [])

    assert proposal.diagnosis == "well_calibrated"
    assert proposal.dispute_count == 0
    assert proposal.proposed_rule_text == sample_rule.description
    assert proposal.proposed_severity == sample_rule.severity