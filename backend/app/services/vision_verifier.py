"""
Improvement 2: Multimodal Vision Asset Verification Service.

Compares submitted campaign images (or images extracted from PDF/DOCX) against
a workspace's Approved Asset Library using an OpenAI-compatible Vision-Language
Model (Groq Llama-4-Scout / Llama-3.2-Vision or Gemini 2.5 Flash), with a
deterministic OpenCV/Pillow fallback when offline or if the VLM times out.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import os
from io import BytesIO
from pathlib import Path
from typing import Any, List, Literal, Mapping, Optional, Sequence, Union

from PIL import Image, ImageStat
from pydantic import BaseModel, Field, ValidationError

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from openai import AsyncOpenAI, OpenAI  # type: ignore

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

logger = logging.getLogger(__name__)


_PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def _provider_base_url() -> "Optional[str]":
    """Base URL for LLM_PROVIDER, matching services/verification.py. None means the OpenAI default."""
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    return _PROVIDER_BASE_URLS.get(provider)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


_VLM_PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
}
_VLM_PROVIDER_MODELS = {"gemini": "gemini-2.5-flash"}


def resolve_vlm_settings(env: Mapping[str, str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (api_key, base_url, model) for the vision checks, from VLM_* variables only.

    Returns (None, None, None) when no VLM_* variable is set, so the caller falls back to the
    LLM_* settings. When VLM_PROVIDER or VLM_API_KEY is set, vision uses its own settings and
    never borrows the text provider's key. A key sent to the wrong provider fails, and leaks.
    """

    def clean(name: str) -> Optional[str]:
        value = (env.get(name) or "").strip()
        return value or None

    provider = (clean("VLM_PROVIDER") or "").lower() or None
    own_key = clean("VLM_API_KEY")
    if provider is None and own_key is None:
        return None, None, None

    base_url = clean("VLM_BASE_URL") or (_VLM_PROVIDER_BASE_URLS.get(provider) if provider else None)
    model = clean("VLM_MODEL") or (_VLM_PROVIDER_MODELS.get(provider) if provider else None)
    return own_key, base_url, model


class ApprovedAssetSpec(BaseModel):
    """Represents a reference asset or visual rule from the Approved Asset Library."""

    asset_id: str = Field(..., description="Unique ID of the approved asset or visual rule")
    name: str = Field(..., description="Human-readable asset name, e.g., 'Primary Wordmark'")
    asset_type: str = Field(
        default="logo",
        description="Type of asset: logo, banner, product_shot, typography, palette_rule",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Local filesystem path to the approved reference image, if available",
    )
    description: str = Field(
        default="",
        description="Visual usage guidelines, safe-zone rules, and allowed variants",
    )
    brand_colors: List[str] = Field(
        default_factory=list,
        description="Approved hex colors associated with this asset (e.g., ['#0F172A', '#3B82F6'])",
    )
    placement_rules: Optional[str] = Field(
        default=None,
        description="Placement/padding/contrast rules (e.g., 'Top-left corner, min 24px clear space')",
    )


class VisualFinding(BaseModel):
    """Strict output schema for a single visual compliance finding."""

    category: Literal["visual"] = "visual"
    severity: Literal["critical", "major", "minor"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., min_length=5)
    evidence_id: str = Field(
        ...,
        description="Matching asset_id or visual rule ID from the Approved Asset Library",
    )
    suggested_edit: str = Field(
        ...,
        min_length=5,
        description="Actionable visual fix for the creator",
    )


class VisionVerificationResponse(BaseModel):
    """Envelope returned by the VLM structured output parser."""

    findings: List[VisualFinding] = Field(default_factory=list)
    used_fallback: bool = Field(default=False)
    model_used: str = Field(default="deterministic-fallback")


# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------


def encode_image_to_data_url(image_source: Union[str, Path, bytes]) -> str:
    """
    Encode a file path or raw image bytes into a base64 `data:image/...;base64,...` URL.
    """
    if isinstance(image_source, (str, Path)):
        path = Path(image_source)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")
        mime_type, _ = mimetypes.guess_type(str(path))
        mime_type = mime_type or "image/png"
        raw_bytes = path.read_bytes()
    elif isinstance(image_source, bytes):
        raw_bytes = image_source
        with Image.open(BytesIO(raw_bytes)) as img:
            fmt = (img.format or "PNG").lower()
            mime_type = f"image/{'jpeg' if fmt == 'jpg' else fmt}"
    else:
        raise TypeError("image_source must be a file path (str/Path) or raw bytes.")

    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _load_pil_image(image_source: Union[str, Path, bytes]) -> Image.Image:
    if isinstance(image_source, (str, Path)):
        return Image.open(image_source).convert("RGB")
    return Image.open(BytesIO(image_source)).convert("RGB")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(c * 2 for c in cleaned)
    if len(cleaned) != 6:
        return (0, 0, 0)
    return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))


def _compute_histogram_distance(img_a: Image.Image, img_b: Image.Image) -> float:
    """
    Compute normalized color histogram distance in [0.0, 1.0] where 0.0 is identical
    and 1.0 is completely dissimilar. Uses OpenCV Bhattacharyya distance if available,
    falling back to Pillow normalized L1 histogram distance.
    """
    if HAS_CV2:
        arr_a = cv2.cvtColor(np.array(img_a.resize((256, 256))), cv2.COLOR_RGB2HSV)
        arr_b = cv2.cvtColor(np.array(img_b.resize((256, 256))), cv2.COLOR_RGB2HSV)
        hist_a = cv2.calcHist([arr_a], [0, 1], None, [32, 32], [0, 180, 0, 256])
        hist_b = cv2.calcHist([arr_b], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist_a, hist_a, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_b, hist_b, 0, 1, cv2.NORM_MINMAX)
        dist = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))
        return max(0.0, min(1.0, dist))

    # Pillow fallback: 3-channel RGB histogram L1 distance
    h_a = img_a.resize((256, 256)).histogram()
    h_b = img_b.resize((256, 256)).histogram()
    sum_a = sum(h_a) or 1
    sum_b = sum(h_b) or 1
    l1 = sum(abs((a / sum_a) - (b / sum_b)) for a, b in zip(h_a, h_b)) / 2.0
    return max(0.0, min(1.0, float(l1)))


def _brand_palette_alignment(img: Image.Image, hex_colors: Sequence[str]) -> float:
    """
    Returns minimum Euclidean RGB distance (normalized 0..1) between dominant/sampled
    colors in `img` and the workspace's approved `hex_colors`.
    """
    if not hex_colors:
        return 0.0
    approved_rgbs = [_hex_to_rgb(c) for c in hex_colors]
    quantized = img.resize((128, 128)).quantize(colors=6).convert("RGB")
    palette_colors = [rgb for _, rgb in (quantized.getcolors(maxcolors=16) or [])]
    if not palette_colors:
        stat = ImageStat.Stat(img)
        palette_colors = [(int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2]))]

    max_dist = math.sqrt(3 * (255**2))
    best_norm_dist = 1.0
    for r1, g1, b1 in palette_colors:
        for r2, g2, b2 in approved_rgbs:
            dist = math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) / max_dist
            if dist < best_norm_dist:
                best_norm_dist = dist
    return best_norm_dist


# ---------------------------------------------------------------------------
# Vision Verifier Service
# ---------------------------------------------------------------------------


class VisionVerifier:
    """
    Multimodal Brand & Visual Asset Verifier.
    """

    DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 25.0,
        client: Optional[Any] = None,
    ) -> None:
        vlm_key, vlm_base_url, vlm_model = resolve_vlm_settings(os.environ)
        if (vlm_key, vlm_base_url, vlm_model) != (None, None, None):
            self.api_key = api_key or vlm_key
            self.base_url = base_url or vlm_base_url
            self.model = (
                model
                or vlm_model
                or (
                    self.DEFAULT_GEMINI_MODEL
                    if (self.base_url and "googleapis" in self.base_url)
                    else self.DEFAULT_GROQ_MODEL
                )
            )
        else:
            self.api_key = (
                api_key
                or os.getenv("LLM_API_KEY")
                or os.getenv("GROQ_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
            self.base_url = base_url or os.getenv("LLM_BASE_URL") or _provider_base_url()
            if not self.base_url and os.getenv("GROQ_API_KEY") and not os.getenv("GEMINI_API_KEY"):
                self.base_url = "https://api.groq.com/openai/v1"
            elif not self.base_url and os.getenv("GEMINI_API_KEY"):
                self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            self.model = (
                model
                or os.getenv("VLM_MODEL")
                or (
                    self.DEFAULT_GEMINI_MODEL
                    if (self.base_url and "googleapis" in self.base_url)
                    else self.DEFAULT_GROQ_MODEL
                )
            )
        # Log the provider and model only. Never log the key.
        logger.info(
            "Vision checks: base_url=%s model=%s key_set=%s",
            self.base_url or "default",
            self.model,
            bool(self.api_key),
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

    def _build_prompt_messages(
        self,
        target_data_url: str,
        approved_assets: Sequence[ApprovedAssetSpec],
        context_note: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        asset_catalog_lines: list[str] = []
        reference_image_blocks: list[dict[str, Any]] = []

        for asset in approved_assets:
            colors_str = ", ".join(asset.brand_colors) if asset.brand_colors else "Not specified"
            placement_str = asset.placement_rules or "Standard brand clear-space rules"
            asset_catalog_lines.append(
                f"- evidence_id: `{asset.asset_id}` | name: {asset.name} ({asset.asset_type}) | "
                f"colors: [{colors_str}] | placement: {placement_str} | rules: {asset.description}"
            )
            if asset.file_path and Path(asset.file_path).exists():
                try:
                    ref_url = encode_image_to_data_url(asset.file_path)
                    reference_image_blocks.append(
                        {
                            "type": "text",
                            "text": f"Reference Approved Asset `{asset.asset_id}` ({asset.name}):",
                        }
                    )
                    reference_image_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": ref_url},
                        }
                    )
                except Exception as exc:
                    logger.warning("Skipping reference image %s: %s", asset.file_path, exc)

        catalog_text = "\n".join(asset_catalog_lines) or "- No specific approved assets provided."
        extra_context = f"\nSubmission context: {context_note}" if context_note else ""

        system_prompt = (
            "You are Inkloom AI's Brand Visual Compliance Auditor. Inspect the submitted campaign image "
            "against the workspace's Approved Asset Library and visual brand guidelines.\n"
            "Check for:\n"
            "1. Outdated, stretched, cropped, low-resolution, or unauthorized logos/watermarks.\n"
            "2. Off-brand color palettes or poor foreground/background logo contrast.\n"
            "3. Violated logo placement, safe-zone padding, or aspect-ratio rules.\n"
            "4. Style adherence: photography, illustration, typography, or layout that conflicts with the visual guidelines in the Approved Asset Library (visual_rule entries).\n"
            "5. Prohibited visual elements: anything a visual_rule entry forbids. Cite the evidence_id of the violated visual_rule entry and quote the rule in the explanation.\n"
            "6. Misleading promotional visuals: fake badges, certifications, prize or money imagery, or claims in the artwork that no approved rule supports.\n\n"
            "Return ONLY valid JSON matching this exact schema:\n"
            "{\n"
            '  "findings": [\n'
            "    {\n"
            '      "category": "visual",\n'
            '      "severity": "critical" | "major" | "minor",\n'
            '      "confidence": 0.0 to 1.0,\n'
            '      "explanation": "Specific description of the visual discrepancy",\n'
            '      "evidence_id": "Exact asset_id from the Approved Asset Library",\n'
            '      "suggested_edit": "Actionable visual fix (e.g., Replace outdated monochrome logo with approved primary wordmark)"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            'If the submitted image complies with all approved assets and rules, return {"findings": []}.'
        )

        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Approved Asset Library:\n{catalog_text}{extra_context}\n\n"
                    "Target Campaign Image to Audit:"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": target_data_url},
            },
            *reference_image_blocks,
        ]

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def _parse_vlm_json(raw_text: str, valid_asset_ids: set[str]) -> List[VisualFinding]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        payload = json.loads(cleaned)
        if isinstance(payload, list):
            payload = {"findings": payload}

        parsed = VisionVerificationResponse.model_validate(payload)
        default_id = next(iter(valid_asset_ids), "visual-guideline-general")
        normalized: List[VisualFinding] = []
        for item in parsed.findings:
            ev_id = item.evidence_id if (not valid_asset_ids or item.evidence_id in valid_asset_ids) else default_id
            normalized.append(
                VisualFinding(
                    category="visual",
                    severity=item.severity,
                    confidence=round(float(item.confidence), 3),
                    explanation=item.explanation,
                    evidence_id=ev_id,
                    suggested_edit=item.suggested_edit,
                )
            )
        return normalized

    def deterministic_fallback(
        self,
        image_source: Union[str, Path, bytes],
        approved_assets: Sequence[ApprovedAssetSpec],
        target_metadata: Optional[Any] = None,
    ) -> VisionVerificationResponse:
        """
        Offline OpenCV/Pillow fallback:
        - Checks minimum image resolution / sharpness.
        - Compares color histogram distance and aspect ratio against reference assets.
        - Checks brand hex palette alignment when brand_colors are configured.
        """
        findings: List[VisualFinding] = []
        target_img = _load_pil_image(image_source)
        width, height = target_img.size
        target_aspect = width / max(height, 1)
        source_name = Path(image_source).name.casefold() if isinstance(image_source, (str, Path)) else ""
        metadata_asset_type = (
            target_metadata.get("asset_type")
            if isinstance(target_metadata, dict)
            else getattr(target_metadata, "asset_type", None)
        )
        compare_lockup = bool(metadata_asset_type) or any(
            marker in source_name for marker in ("logo", "wordmark", "banner")
        )

        default_asset = approved_assets[0] if approved_assets else None
        default_evidence_id = default_asset.asset_id if default_asset else "visual-quality-baseline"

        # 1. Resolution / pixel density check
        if width < 400 or height < 200:
            findings.append(
                VisualFinding(
                    category="visual",
                    severity="major",
                    confidence=0.92,
                    explanation=(
                        f"Submitted asset resolution ({width}x{height}px) is below the minimum "
                        "400x200px threshold required for crisp campaign rendering."
                    ),
                    evidence_id=default_evidence_id,
                    suggested_edit=(
                        f"Export a high-resolution version (at least 1200px wide) matching "
                        f"{default_asset.name if default_asset else 'approved brand specs'}."
                    ),
                )
            )

        # 2. Compare against each Approved Asset reference image and palette
        for asset in approved_assets:
            if compare_lockup and asset.file_path and Path(asset.file_path).exists():
                try:
                    ref_img = _load_pil_image(asset.file_path)
                    ref_w, ref_h = ref_img.size
                    ref_aspect = ref_w / max(ref_h, 1)

                    # Check aspect ratio distortion for standalone logo/banner comparisons
                    if asset.asset_type in {"logo", "banner"}:
                        aspect_delta = abs(target_aspect - ref_aspect) / max(ref_aspect, 0.01)
                        if aspect_delta > 0.35:
                            findings.append(
                                VisualFinding(
                                    category="visual",
                                    severity="major",
                                    confidence=0.81,
                                    explanation=(
                                        f"Aspect ratio ({target_aspect:.2f}) deviates significantly from "
                                        f"approved '{asset.name}' ({ref_aspect:.2f}), indicating possible "
                                        "stretching, cropping, or wrong lockup variant."
                                    ),
                                    evidence_id=asset.asset_id,
                                    suggested_edit=(
                                        f"Replace distorted asset with uncropped '{asset.name}' "
                                        f"preserving the {ref_w}:{ref_h} aspect ratio."
                                    ),
                                )
                            )

                    hist_dist = _compute_histogram_distance(target_img, ref_img)
                    if hist_dist > 0.68:
                        findings.append(
                            VisualFinding(
                                category="visual",
                                severity="major",
                                confidence=round(min(0.95, 0.55 + hist_dist * 0.4), 2),
                                explanation=(
                                    f"Color distribution diverges strongly from approved '{asset.name}' "
                                    f"(histogram distance {hist_dist:.2f} > 0.68), suggesting an outdated "
                                    "monochrome or unapproved color variant."
                                ),
                                evidence_id=asset.asset_id,
                                suggested_edit=(
                                    f"Replace the off-brand visual with the approved '{asset.name}' "
                                    f"({asset.description or 'official brand asset'})."
                                ),
                            )
                        )
                except Exception as exc:
                    logger.warning("Fallback comparison failed for asset %s: %s", asset.asset_id, exc)

            if asset.brand_colors:
                palette_dist = _brand_palette_alignment(target_img, asset.brand_colors)
                if palette_dist > 0.38:
                    findings.append(
                        VisualFinding(
                            category="visual",
                            severity="minor",
                            confidence=0.76,
                            explanation=(
                                f"Dominant image colors do not align with approved palette "
                                f"({', '.join(asset.brand_colors)}) for '{asset.name}'."
                            ),
                            evidence_id=asset.asset_id,
                            suggested_edit=(
                                f"Update graphic accents and background fills to use approved brand colors "
                                f"({', '.join(asset.brand_colors)})."
                            ),
                        )
                    )

        return VisionVerificationResponse(
            findings=findings,
            used_fallback=True,
            model_used="deterministic-opencv-pillow",
        )

    def verify(
        self,
        image_source: Union[str, Path, bytes],
        approved_assets: Sequence[ApprovedAssetSpec],
        context_note: Optional[str] = None,
        target_metadata: Optional[Any] = None,
    ) -> VisionVerificationResponse:
        """
        Synchronously verify a campaign image against the Approved Asset Library.
        Automatically falls back to deterministic OpenCV/Pillow checks if `LLM_API_KEY`
        is missing or if the VLM request fails / times out.
        """
        image_assets = [a for a in approved_assets if a.file_path]
        client = self._get_client()
        if client is None:
            logger.info("LLM_API_KEY not configured; running deterministic visual fallback.")
            return self.deterministic_fallback(
                image_source, image_assets, target_metadata=target_metadata
            )

        try:
            target_data_url = encode_image_to_data_url(image_source)
            messages = self._build_prompt_messages(
                target_data_url=target_data_url,
                approved_assets=approved_assets,
                context_note=context_note,
            )

            try:
                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=600,
                    response_format={"type": "json_object"},
                    timeout=self.timeout_seconds,
                )
            except Exception as req_exc:
                err_text = str(req_exc)
                if "json_validate_failed" not in err_text and "response_format" not in err_text:
                    raise
                logger.warning(
                    "VLM JSON response_format rejected by %s (%s); retrying without response_format",
                    self.model,
                    req_exc,
                )
                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=600,
                    timeout=self.timeout_seconds,
                )
            raw_content = completion.choices[0].message.content or '{"findings": []}'
            valid_ids = {a.asset_id for a in approved_assets}
            findings = self._parse_vlm_json(raw_content, valid_ids)

            return VisionVerificationResponse(
                findings=findings,
                used_fallback=False,
                model_used=self.model,
            )
        except (TimeoutError, ValidationError, json.JSONDecodeError, Exception) as exc:
            logger.warning(
                "VLM verification failed (%s: %s); falling back to deterministic checks.",
                type(exc).__name__,
                exc,
            )
            return self.deterministic_fallback(image_source, image_assets)



# ---------------------------------------------------------------------------
# Module-level entry point used by app.services.orchestrator
# ---------------------------------------------------------------------------


def _load_approved_assets(workspace_id: str) -> List[ApprovedAssetSpec]:
    """Read approved visual assets (KnowledgeEntry rows with entry_type='asset') for a workspace.

    Expired assets and assets whose image file is missing on disk are skipped, so the
    VLM never receives a broken or stale reference.
    """
    from datetime import date

    from app.database import SessionLocal
    from app.models import KnowledgeEntry, Workspace

    db = SessionLocal()
    try:
        ws = db.get(Workspace, str(workspace_id))
        palette = list(ws.brand_palette_hex or []) if ws else []
        rows = (
            db.query(KnowledgeEntry)
            .filter(
                KnowledgeEntry.workspace_id == str(workspace_id),
                KnowledgeEntry.entry_type.in_(["asset", "rule"]),
            )
            .all()
        )
        today = date.today()
        specs: List[ApprovedAssetSpec] = []
        for row in rows:
            if row.expiry_date is not None and row.expiry_date < today:
                continue
            if row.entry_type == "rule":
                # Visual rules (e.g. "no competitor logos") go to the VLM prompt only. They have
                # no image, so the pixel-level fallback never sees them.
                if (row.category or "").lower() == "visual":
                    specs.append(
                        ApprovedAssetSpec(
                            asset_id=row.id,
                            name=row.title or row.id,
                            asset_type="visual_rule",
                            file_path=None,
                            description=row.rule_text or "",
                            brand_colors=[],
                            placement_rules=None,
                        )
                    )
                continue
            if not row.image_path or not Path(row.image_path).is_file():
                continue
            specs.append(
                ApprovedAssetSpec(
                    asset_id=row.id,
                    name=row.title or row.id,
                    asset_type=row.asset_type or "logo",
                    file_path=row.image_path,
                    description=row.rule_text or row.usage_rules or "",
                    brand_colors=palette,
                    placement_rules=row.usage_rules or None,
                )
            )
        return specs
    finally:
        db.close()


def verify_images(image_units: Sequence[Any], workspace_id: str) -> List[Any]:
    """Compare each uploaded image against the workspace's Approved Asset Library.

    Returns shared-schema Finding objects (category 'visual') so the orchestrator can merge
    them with text findings. With no approved assets, no LLM key, or any VLM failure, the
    verifier falls back to deterministic OpenCV/Pillow checks.
    """
    from uuid import uuid4

    from app.schemas import EvidenceItem as SharedEvidence
    from app.schemas import Finding as SharedFinding

    assets = _load_approved_assets(workspace_id)
    if not assets:
        return []

    verifier = VisionVerifier()
    findings: List[Any] = []
    for unit in image_units:
        image_path = getattr(unit.metadata, "image_path", None) or unit.content
        if not image_path or not Path(image_path).is_file():
            continue
        try:
            unit_metadata = getattr(unit, "metadata", None)
            asset_type = (
                unit_metadata.get("asset_type")
                if isinstance(unit_metadata, dict)
                else getattr(unit_metadata, "asset_type", None)
            )
            if asset_type:
                response = verifier.verify(
                    image_path, assets, target_metadata=unit_metadata
                )
            else:
                response = verifier.verify(image_path, assets)
        except Exception:  # one bad image must not break the whole analysis
            logger.exception("Vision verification crashed for unit %s", unit.unit_id)
            continue

        # Show the uploaded file name. The image_path is a temp file and must not reach the reviewer.
        shown_name = getattr(unit.metadata, "source_name", None) or "uploaded image"
        label = f"[Image: {shown_name}]"
        for vf in response.findings:
            asset = next((a for a in assets if a.asset_id == vf.evidence_id), assets[0])
            findings.append(
                SharedFinding(
                    finding_id=f"vis_{uuid4().hex[:16]}",
                    unit_id=unit.unit_id,
                    category="visual",
                    severity=vf.severity,
                    original_span=label,
                    explanation=vf.explanation,
                    evidence=SharedEvidence(
                        evidence_id=asset.asset_id,
                        source_type="guideline" if asset.asset_type == "visual_rule" else "asset",
                        title=asset.name,
                        content=asset.description or asset.name,
                        owner=None,
                        expiry_date=None,
                    ),
                    suggested_edit=vf.suggested_edit,
                    confidence=vf.confidence,
                )
            )
    return findings
