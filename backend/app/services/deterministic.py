"""Owner: Riyesh. Deterministic checks for workspace content.

This module follows the existing Inkloom AI service contract:
    run_checks(units, workspace_id) -> List[Finding]

Workspace brand colours and banned terms are loaded from the existing SQLAlchemy
models. Content metadata is read only from the shared schemas in app.schemas.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import List, Optional, Tuple

from app.database import SessionLocal
from app.models import KnowledgeEntry, Workspace
from app.schemas import ContentUnit, EvidenceItem, Finding

# An organisation name followed by a company suffix, e.g. "DreamTech Organisation", "Acme Ltd".
_ORG_NAME = re.compile(
    r"\b((?:[A-Z][\w&.-]*)(?:\s+[A-Z][\w&.-]*){0,2})\s+"
    r"(Organi[sz]ation|Organi[sz]ations|Inc|Ltd|LLC|Corp|Corporation|Company|Technologies|Group)\b"
)
# Words that do not identify a brand when comparing names.
_GENERIC_NAME_WORDS = {"cloud", "workspace", "group", "company", "technologies", "inc", "ltd", "llc", "corp"}
_COMMON_MARKETING_WORDS = {
    "get", "free", "click", "win", "limited", "time", "just", "claim", "now",
    "earn", "daily", "no", "work", "skills", "money", "too", "be", "winner",
    "with", "the", "and", "for", "you", "your", "our", "logo", "wordmark", "brand",
}
_MARKETING_COPY_MARKERS = {
    "get", "free", "click", "win", "limited", "claim", "offer", "earn", "discount",
    "buy", "shop", "save", "sale", "exclusive",
}

# The shared schema does not currently store per-workspace image-size settings.
# These conservative defaults can be changed centrally if the team defines a
# specific image-size policy.
MIN_IMAGE_WIDTH = 1
MIN_IMAGE_HEIGHT = 1

# The shared schema does not include font size, so contrast is checked against
# the WCAG AA normal-text threshold. Large-text handling can be added when
# font-size metadata becomes part of UnitMetadata.
MIN_CONTRAST_RATIO = 4.5


def normalise_for_checks(text: str) -> str:
    """Normalize text using Unicode NFKC for robust character and punctuation matching."""
    return unicodedata.normalize("NFKC", text)


def run_checks(units: List[ContentUnit], workspace_id: str) -> List[Finding]:
    """Run deterministic checks and return findings for the supplied units."""
    palette, banned_terms = _load_workspace_rules(workspace_id)
    findings: List[Finding] = []

    workspace_name = _load_workspace_name(workspace_id)

    for unit in units:
        findings.extend(_check_color_contrast(unit, palette))
        findings.extend(_check_image_dimensions(unit))
        findings.extend(_check_image_alt_text(unit))
        findings.extend(_check_banned_terms(unit, banned_terms))
        findings.extend(_check_brand_name(unit, workspace_name))
        findings.extend(_check_tone(unit))

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


def _load_workspace_name(workspace_id: str) -> str:
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        return workspace.name if workspace else ""
    finally:
        db.close()


def _check_brand_name(unit: ContentUnit, workspace_name: str) -> List[Finding]:
    """Flag text that names a different organisation than this workspace.

    Only names followed by a company suffix are checked, so ordinary capitalised phrases are
    not flagged. The check passes when the name shares a distinctive word with the workspace.
    """
    if unit.unit_type != "text" or not unit.content or not workspace_name:
        return []

    workspace_words = {
        w.casefold() for w in re.findall(r"[A-Za-z0-9]+", workspace_name) if len(w) > 1
    } - _GENERIC_NAME_WORDS
    if not workspace_words:
        return []

    findings: List[Finding] = []
    suffix_name_tokens = set()
    for match in _ORG_NAME.finditer(unit.content):
        name_prefix = match.group(1)
        leading_fragments = re.match(r"(?:[A-Za-z]\s+)+", name_prefix)
        trim_length = leading_fragments.end() if leading_fragments else 0
        org_name = f"{name_prefix[trim_length:]} {match.group(2)}"
        original_start = match.start(1) + trim_length
        org_words = {w.casefold() for w in re.findall(r"[A-Za-z0-9]+", org_name)}
        suffix_name_tokens.update(org_words)
        if org_words & workspace_words:
            continue  # the name refers to this workspace's brand
        findings.append(
            _make_finding(
                unit=unit,
                category="brand",
                severity="major",
                original_span=org_name,
                explanation=(
                    f"The content names '{org_name}', which does not match this workspace's brand "
                    f"'{workspace_name}'. Confirm the material belongs to this brand before publishing."
                ),
                suggested_edit=None,
                rule_key=f"brand-mismatch:{org_name.casefold()}:{original_start}",
            )
        )

    content_words = {
        word.casefold() for word in re.findall(r"[A-Za-z0-9]+", unit.content)
    }
    if not content_words & _MARKETING_COPY_MARKERS:
        return findings

    lines = unit.content.splitlines()
    first_three_lines = lines[:3]
    logo_area_tokens = {
        token.casefold()
        for line in lines
        if re.search(r"\b(?:logo|wordmark|brand)\b", line, flags=re.IGNORECASE)
        for token in re.findall(r"\b[A-Z][A-Za-z0-9]{1,}\b", line)
    }
    checked_tokens = set()
    for line in first_three_lines:
        for token in re.findall(r"\b[A-Z][A-Za-z0-9]{1,}\b", line):
            token_key = token.casefold()
            if (
                token_key in _COMMON_MARKETING_WORDS
                or token_key in workspace_words
                or token_key in suffix_name_tokens
                or token_key in checked_tokens
            ):
                continue

            is_camel_case = any(char.isupper() for char in token[1:])
            if not is_camel_case and token_key not in logo_area_tokens:
                continue

            checked_tokens.add(token_key)
            findings.append(
                _make_finding(
                    unit=unit,
                    category="brand",
                    severity="major",
                    original_span=token,
                    explanation=(
                        f"The content names '{token}', which does not match this workspace's brand "
                        f"'{workspace_name}'. Confirm the material belongs to this brand before publishing."
                    ),
                    suggested_edit=None,
                    rule_key=f"brand-mismatch-token:{token_key}:{lines.index(line)}",
                )
            )
    return findings


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
    if getattr(unit.metadata, "embedded", False):
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


def _map_norm_span(orig_text: str, norm_text: str, start: int, end: int) -> Tuple[int, int]:
    if len(orig_text) == len(norm_text):
        return start, end
    cur_norm = 0
    orig_start = 0
    orig_end = len(orig_text)
    start_found = False
    for i, ch in enumerate(orig_text):
        if cur_norm >= start and not start_found:
            orig_start = i
            start_found = True
        cur_norm += len(unicodedata.normalize("NFKC", ch))
        if cur_norm >= end:
            orig_end = i + 1
            break
    return orig_start, orig_end


def _check_tone(unit: ContentUnit) -> List[Finding]:
    """Flag exclamation marks in customer-facing copy as one tone finding per ContentUnit."""
    if unit.unit_type != "text" or not unit.content:
        return []

    source_lines = unit.content.splitlines()
    check_lines = normalise_for_checks(unit.content).splitlines()
    flagged_lines = [
        source_line
        for source_line, check_line in zip(source_lines, check_lines)
        if "!" in check_line
    ]
    if not flagged_lines:
        return []

    original_span = flagged_lines[0].strip()
    if original_span not in unit.content and flagged_lines[0] in unit.content:
        original_span = flagged_lines[0]

    line_count = len(flagged_lines)
    line_word = "line" if line_count == 1 else "lines"
    explanation = (
        f"Exclamation marks appear on {line_count} {line_word}, which can make the copy "
        "feel overly emphatic and conflict with the brand voice."
    )
    return [
        _make_finding(
            unit=unit,
            category="tone",
            severity="minor",
            original_span=original_span,
            explanation=explanation,
            rule_key=f"tone-exclamation:{len(flagged_lines)}",
            suggested_edit=re.sub(r"[!！]+", "", original_span).strip(),
        )
    ]


def _check_banned_terms(
    unit: ContentUnit, banned_terms: List[KnowledgeEntry]
) -> List[Finding]:
    """Find normalized whole-term occurrences and preserve exact source spans."""
    if unit.unit_type != "text" or not unit.content:
        return []

    findings: List[Finding] = []
    norm_content = normalise_for_checks(unit.content)

    for entry in banned_terms:
        phrase = (entry.title or "").strip()
        if not phrase:
            continue

        normalized_phrase = "".join(
            char for char in normalise_for_checks(phrase).casefold() if char.isalnum() or char in "'%"
        )
        if not normalized_phrase:
            continue

        separated_chars = r"(?:(?!['%])[\W_])*".join(
            re.escape(char) for char in normalized_phrase
        )
        pattern = re.compile(
            rf"(?<!\w){separated_chars}(?!\w)",
            flags=re.IGNORECASE,
        )

        for match in pattern.finditer(norm_content):
            start, end = _map_norm_span(unit.content, norm_content, match.start(), match.end())
            original_span = unit.content[start:end]
            if not original_span or original_span not in unit.content:
                original_span = match.group(0)

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
                    original_span=original_span,
                    explanation=(
                        f"'{original_span}' is listed as a banned term for this "
                        f"workspace. {entry.rule_text}"
                    ).strip(),
                    evidence=evidence,
                    suggested_edit=None,
                    rule_key=f"banned-term:{entry.id}:{start}:{end}",
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