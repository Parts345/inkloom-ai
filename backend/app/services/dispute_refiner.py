"""
Improvement 3: Dispute-Driven Rule Refinement Service.

Clusters creator dispute reasons on frequently contested brand rules and uses an
LLM with strict Pydantic JSON enforcement to diagnose ambiguity/over-breadth/staleness
and synthesize concrete updated rule wording—with a deterministic offline fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from typing import Any, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError

try:
    from openai import OpenAI  # type: ignore

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class RuleEntryInput(BaseModel):
    """Input representation of a brand governance rule."""

    rule_id: str
    title: str
    description: str
    category: str = "general"
    severity: Literal["critical", "major", "minor"] = "major"


class CreatorDisputeRecord(BaseModel):
    """Single creator dispute logged against a flagged finding."""

    dispute_reason: str = Field(..., min_length=1)
    flagged_text: str = Field(default="")
    submission_title: str = Field(default="Untitled Submission")


class RuleRefinementProposalOutput(BaseModel):
    """Strict output schema for a synthesized rule refinement proposal."""

    rule_id: str
    cluster_summary: str = Field(
        ...,
        min_length=10,
        description="Concise synthesis of why creators are disputing this rule",
    )
    diagnosis: Literal["ambiguous", "overly_broad", "outdated", "well_calibrated"] = Field(
        ...,
        description="Root-cause diagnosis of the rule's friction",
    )
    proposed_rule_text: str = Field(
        ...,
        min_length=10,
        description="Concrete, actionable updated rule description with clear exceptions/boundaries",
    )
    proposed_severity: Literal["critical", "major", "minor"] = Field(
        ...,
        description="Recommended severity after calibration",
    )
    rationale: str = Field(
        ...,
        min_length=10,
        description="Explanation of how the rewrite resolves creator disputes without weakening brand safety",
    )
    dispute_count: int = Field(default=0, ge=0)
    used_fallback: bool = Field(default=False)
    model_used: str = Field(default="deterministic-fallback")


# ---------------------------------------------------------------------------
# Dispute Refiner Service
# ---------------------------------------------------------------------------


class DisputeRefiner:
    """
    Synthesizes creator dispute clusters into calibrated brand governance rule updates.
    """

    DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

    _STOPWORDS = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "have",
        "were",
        "been",
        "when",
        "what",
        "your",
        "their",
        "about",
        "into",
        "only",
        "also",
        "they",
        "them",
        "rule",
        "flagged",
        "text",
        "used",
        "using",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 20.0,
        client: Optional[Any] = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        if not self.base_url and os.getenv("GROQ_API_KEY") and not os.getenv("GEMINI_API_KEY"):
            self.base_url = "[https://api.groq.com/openai/v1](https://api.groq.com/openai/v1)"
        elif not self.base_url and os.getenv("GEMINI_API_KEY"):
            self.base_url = "[https://generativelanguage.googleapis.com/v1beta/openai/](https://generativelanguage.googleapis.com/v1beta/openai/)"

        self.model = (
            model
            or os.getenv("LLM_MODEL")
            or (
                self.DEFAULT_GEMINI_MODEL
                if (self.base_url and "googleapis" in self.base_url)
                else self.DEFAULT_GROQ_MODEL
            )
        )
        self.timeout_seconds = timeout_seconds
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not HAS_OPENAI or not self.api_key:
            return None
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": self.timeout_seconds,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def _build_messages(
        self,
        rule: RuleEntryInput,
        disputes: Sequence[CreatorDisputeRecord],
    ) -> list[dict[str, str]]:
        dispute_lines = []
        for idx, d in enumerate(disputes, start=1):
            dispute_lines.append(
                f"{idx}. Submission: \"{d.submission_title}\" | "
                f"Flagged snippet: \"{d.flagged_text}\" | "
                f"Creator dispute reason: \"{d.dispute_reason}\""
            )
        disputes_block = "\n".join(dispute_lines) or "No dispute records supplied."

        system_prompt = (
            "You are Inkloom AI's Brand Governance Policy Architect. Analyze recurring creator disputes "
            "against a brand compliance rule and synthesize a clearer, better-calibrated rule rewrite.\n\n"
            "Return ONLY valid JSON matching this exact schema:\n"
            "{\n"
            '  "cluster_summary": "Concise synthesis of the recurring themes across creator disputes",\n'
            '  "diagnosis": "ambiguous" | "overly_broad" | "outdated" | "well_calibrated",\n'
            '  "proposed_rule_text": "Concrete, unambiguous updated rule wording with explicit scope/exceptions",\n'
            '  "proposed_severity": "critical" | "major" | "minor",\n'
            '  "rationale": "Why this update reduces false-positive disputes while protecting brand standards"\n'
            "}"
        )

        user_prompt = (
            f"Rule ID: {rule.rule_id}\n"
            f"Title: {rule.title}\n"
            f"Category: {rule.category}\n"
            f"Current Severity: {rule.severity}\n"
            f"Current Rule Description: {rule.description}\n\n"
            f"Creator Disputes ({len(disputes)} total):\n{disputes_block}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def deterministic_fallback(
        self,
        rule: RuleEntryInput,
        disputes: Sequence[CreatorDisputeRecord],
    ) -> RuleRefinementProposalOutput:
        """
        Offline keyword & heuristic fallback when `LLM_API_KEY` is unavailable or fails.
        """
        if not disputes:
            return RuleRefinementProposalOutput(
                rule_id=rule.rule_id,
                cluster_summary="No creator disputes have been logged against this rule yet.",
                diagnosis="well_calibrated",
                proposed_rule_text=rule.description,
                proposed_severity=rule.severity,
                rationale="Zero disputes recorded; current rule wording and severity remain appropriate.",
                dispute_count=0,
                used_fallback=True,
                model_used="deterministic-heuristic",
            )

        all_reasons = " ".join(d.dispute_reason.lower() for d in disputes)

        # Heuristic root-cause classification
        if any(k in all_reasons for k in ("old", "outdated", "deprecated", "rebrand", "new product", "2025", "2026")):
            diagnosis: Literal["ambiguous", "overly_broad", "outdated", "well_calibrated"] = "outdated"
        elif any(k in all_reasons for k in ("context", "quote", "educational", "comparison", "exception", "false positive", "internal")):
            diagnosis = "overly_broad"
        else:
            diagnosis = "ambiguous"

        # Extract top recurring keywords for a grounded cluster summary
        tokens = [
            tok
            for tok in re.findall(r"[a-z]{4,}", all_reasons)
            if tok not in self._STOPWORDS
        ]
        top_terms = [word for word, _ in Counter(tokens).most_common(4)]
        themes_str = ", ".join(top_terms) if top_terms else "edge-case context and scope"

        sample_snippets = [d.flagged_text for d in disputes if d.flagged_text][:2]
        snippet_note = (
            f" Commonly contested phrases include: {', '.join(repr(s) for s in sample_snippets)}."
            if sample_snippets
            else ""
        )

        cluster_summary = (
            f"Across {len(disputes)} creator dispute(s) on '{rule.title}', creators consistently "
            f"contested flags involving {themes_str}.{snippet_note}"
        )

        if diagnosis == "overly_broad":
            proposed_rule_text = (
                f"{rule.description.rstrip('.')} — Applies only to external customer-facing claims; "
                "exclude direct quotations, comparative benchmarks with citations, and approved educational context."
            )
            proposed_severity: Literal["critical", "major", "minor"] = (
                "minor" if rule.severity == "major" else rule.severity
            )
        elif diagnosis == "outdated":
            proposed_rule_text = (
                f"{rule.description.rstrip('.')} — Updated to permit current approved product terminology "
                "and post-rebrand nomenclature."
            )
            proposed_severity = rule.severity
        else:
            proposed_rule_text = (
                f"{rule.description.rstrip('.')} — Clarification: flag only explicit violations where "
                "required disclosures or approved terminology are omitted."
            )
            proposed_severity = rule.severity

        rationale = (
            f"Rule diagnosed as '{diagnosis}' based on {len(disputes)} dispute record(s) citing {themes_str}. "
            "Adding explicit boundary conditions reduces false-positive friction for creators."
        )

        return RuleRefinementProposalOutput(
            rule_id=rule.rule_id,
            cluster_summary=cluster_summary,
            diagnosis=diagnosis,
            proposed_rule_text=proposed_rule_text,
            proposed_severity=proposed_severity,
            rationale=rationale,
            dispute_count=len(disputes),
            used_fallback=True,
            model_used="deterministic-heuristic",
        )

    def refine_rule(
        self,
        rule: RuleEntryInput,
        disputes: Sequence[CreatorDisputeRecord],
    ) -> RuleRefinementProposalOutput:
        """
        Synthesize a structured RuleRefinementProposal from a rule and its creator disputes.
        """
        if not disputes:
            return self.deterministic_fallback(rule, disputes)

        client = self._get_client()
        if client is None:
            logger.info("LLM_API_KEY not configured; using deterministic dispute refinement fallback.")
            return self.deterministic_fallback(rule, disputes)

        try:
            messages = self._build_messages(rule, disputes)
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
                timeout=self.timeout_seconds,
            )
            raw_text = completion.choices[0].message.content or "{}"
            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:].strip()

            data = json.loads(cleaned)
            data["rule_id"] = rule.rule_id
            data["dispute_count"] = len(disputes)
            data["used_fallback"] = False
            data["model_used"] = self.model

            return RuleRefinementProposalOutput.model_validate(data)
        except (TimeoutError, ValidationError, json.JSONDecodeError, Exception) as exc:
            logger.warning(
                "Dispute refinement LLM call failed (%s: %s); falling back to heuristic synthesis.",
                type(exc).__name__,
                exc,
            )
            return self.deterministic_fallback(rule, disputes)