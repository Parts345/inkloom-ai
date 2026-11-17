"""Owner: Riyesh. Deterministic checks for workspace content.

This module follows the existing Inkloom AI service contract:
    run_checks(units, workspace_id) -> List[Finding]

Workspace brand colours and banned terms are loaded from the existing SQLAlchemy
models. Content metadata is read only from the shared schemas in app.schemas.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Tuple

from app.database import SessionLocal
from app.models import KnowledgeEntry, Workspace
from app.schemas import ContentUnit, EvidenceItem, Finding

# The shared schema does not currently store per-workspace image-size settings.
# These conservative defaults can be changed centrally if the team defines a
# specific image-size policy.
MIN_IMAGE_WIDTH = 1
MIN_IMAGE_HEIGHT = 1

# The shared schema does not include font size, so contrast is checked against
# the WCAG AA normal-text threshold. Large-text handling can be added when
# font-size metadata becomes part of UnitMetadata.
MIN_CONTRAST_RATIO = 4.5


def run_checks(units: List[ContentUnit], workspace_id: str) -> List[Finding]:
    """Run deterministic checks and return findings for the supplied units."""
    palette, banned_terms = _load_workspace_rules(workspace_id)
    findings: List[Finding] = []

    for unit in units:
        findings.extend(_check_color_contrast(unit, palette))
        findings.extend(_check_image_dimensions(unit))
        findings.extend(_check_image_alt_text(unit))
        findings.extend(_check_banned_terms(unit, banned_terms))

    return findings


def _load_workspace_rules(
    workspace_id: str,
) -> Tuple[List[str], List[KnowledgeEntry]]:
    """Load the workspace palette and active banned-term knowledge entries."""
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        palette = list(workspace.brand_palette_hex or []) if workspace else []

        terms = (
            db.query(KnowledgeEntry)
            .filter(
                KnowledgeEntry.workspace_id == workspace_id,
                KnowledgeEntry.entry_type == "banned_term",
            )
            .all()
        )
        return palette, terms
    finally:
        db.close()


def _check_color_contrast(
    unit: ContentUnit, brand_palette_hex: List[str]
) -> List[Finding]:
    """Check extracted foreground/background pairs against WCAG AA normal text."""
    findings: List[Finding] = []

    for pair in unit.metadata.color_pairs:
        foreground = pair.foreground
        background = pair.background
        ratio = _contrast_ratio(foreground, background)

        if ratio >= MIN_CONTRAST_RATIO:
            continue

        suggested_edit = None
        best_candidate: Optional[Tuple[str, float]] = None
        for candidate in brand_palette_hex:
            if not _is_hex_color(candidate):
                continue
            candidate_ratio = _contrast_ratio(candidate, background)
            if candidate_ratio >= MIN_CONTRAST_RATIO:
                if best_candidate is None or candidate_ratio > best_candidate[1]:
                    best_candidate = (candidate.upper(), candidate_ratio)

        if best_candidate:
            suggested_edit = (
                f"Change the text colour to {best_candidate[0]} "
                f"(contrast ratio {best_candidate[1]:.2f}:1)."
            )

        findings.append(
            _make_finding(
                unit=unit,
                category="visual",
                severity="major",
                original_span=f"{foreground} on {background}",
                explanation=(
                    f"The text/background contrast ratio is {ratio:.2f}:1; "
                    f"WCAG AA requires at least {MIN_CONTRAST_RATIO:.1f}:1 "
                    "for normal-sized text."
                ),
                suggested_edit=suggested_edit,
                rule_key=f"contrast:{foreground.upper()}:{background.upper()}",
            )
        )

    return findings


def _check_image_dimensions(unit: ContentUnit) -> List[Finding]:
    """Reject image dimensions that are missing, zero or otherwise unusable."""
    if unit.unit_type != "image":
        return []

    dimensions = unit.metadata.dimensions
    if dimensions is None:
        return [
            _make_finding(
                unit=unit,
                category="visual",
                severity="minor",
                original_span=unit.content,
                explanation=(
                    "Image dimensions were not provided by extraction, so the "
                    "image size could not be checked."
                ),
                suggested_edit="Provide image width and height in the extracted metadata.",
                rule_key="image-dimensions-missing",
            )
        ]

    if dimensions.width >= MIN_IMAGE_WIDTH and dimensions.height >= MIN_IMAGE_HEIGHT:
        return []

    return [
        _make_finding(
            unit=unit,
            category="visual",
            severity="major",
            original_span=f"{dimensions.width}x{dimensions.height}",
            explanation=(
                "The extracted image has an invalid or zero width/height and "
                "cannot be reliably displayed."
            ),
            suggested_edit="Replace the image with a valid image file and re-upload it.",
            rule_key=f"image-dimensions:{dimensions.width}x{dimensions.height}",
        )
    ]


def _check_image_alt_text(unit: ContentUnit) -> List[Finding]:
    """Flag an image when extraction did not provide any alt-text value."""
    if unit.unit_type != "image":
        return []

    alt_text = unit.metadata.alt_text
    if alt_text is not None and alt_text.strip():
        return []

    return [
        _make_finding(
            unit=unit,
            category="accessibility",
            severity="major",
            original_span=unit.content,
            explanation=(
                "No alternative text was supplied for this image. Informative "
                "images should have a concise text alternative; decorative "
                "images may intentionally use empty alternative text."
            ),
            suggested_edit="Add concise alt text describing the image's meaningful content.",
            rule_key="image-alt-text-missing",
        )
    ]


def _check_banned_terms(
    unit: ContentUnit, banned_terms: List[KnowledgeEntry]
) -> List[Finding]:
    """Find every whole-term occurrence while preserving its original casing."""
    if unit.unit_type != "text" or not unit.content:
        return []

    findings: List[Finding] = []
    for entry in banned_terms:
        phrase = (entry.title or "").strip()
        if not phrase:
            continue

        pattern = re.compile(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            flags=re.IGNORECASE,
        )

        for match in pattern.finditer(unit.content):
            evidence = EvidenceItem(
                evidence_id=entry.id,
                source_type="term",
                title=entry.title,
                content=entry.rule_text,
                owner=entry.owner,
                expiry_date=entry.expiry_date,
            )
            findings.append(
                _make_finding(
                    unit=unit,
                    category="terminology",
                    severity=_valid_severity(entry.severity),
                    original_span=match.group(0),
                    explanation=(
                        f"'{match.group(0)}' is listed as a banned term for this "
                        f"workspace. {entry.rule_text}"
                    ).strip(),
                    evidence=evidence,
                    suggested_edit=None,
                    rule_key=f"banned-term:{entry.id}:{match.start()}:{match.end()}",
                )
            )

    return findings


def _make_finding(
    *,
    unit: ContentUnit,
    category: str,
    severity: str,
    original_span: str,
    explanation: str,
    rule_key: str,
    evidence: Optional[EvidenceItem] = None,
    suggested_edit: Optional[str] = None,
) -> Finding:
    """Create a stable finding ID from the unit, rule and matched span."""
    raw_id = "|".join(
        (unit.unit_id, category, rule_key, original_span, explanation)
    )
    digest = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()
    return Finding(
        finding_id=f"det_{digest}",
        unit_id=unit.unit_id,
        category=category,
        severity=severity,
        original_span=original_span,
        explanation=explanation,
        evidence=evidence,
        suggested_edit=suggested_edit,
        confidence=1.0,
    )


def _valid_severity(value: Optional[str]) -> str:
    """Keep database severity values compatible with the Finding schema."""
    return value if value in {"critical", "major", "minor"} else "minor"


def _is_hex_color(value: str) -> bool:
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""))


def _contrast_ratio(color_a: str, color_b: str) -> float:
    """Calculate WCAG contrast ratio using sRGB relative luminance."""
    luminance_a = _relative_luminance(color_a)
    luminance_b = _relative_luminance(color_b)
    lighter = max(luminance_a, luminance_b)
    darker = min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(hex_color: str) -> float:
    """Convert an sRGB hex colour to W3C relative luminance."""
    if not _is_hex_color(hex_color):
        raise ValueError(f"Invalid HEX colour: {hex_color!r}")

    channels = [
        int(hex_color[index : index + 2], 16) / 255.0
        for index in (1, 3, 5)
    ]

    def linearize(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue