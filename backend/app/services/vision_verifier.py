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
from typing import Any, List, Literal, Optional, Sequence, Union

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


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


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

    DEFAULT_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 25.0,
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
            "3. Violated logo placement, safe-zone padding, or aspect-ratio rules.\n\n"
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
            if asset.file_path and Path(asset.file_path).exists():
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
    ) -> VisionVerificationResponse:
        """
        Synchronously verify a campaign image against the Approved Asset Library.
        Automatically falls back to deterministic OpenCV/Pillow checks if `LLM_API_KEY`
        is missing or if the VLM request fails / times out.
        """
        client = self._get_client()
        if client is None:
            logger.info("LLM_API_KEY not configured; running deterministic visual fallback.")
            return self.deterministic_fallback(image_source, approved_assets)

        try:
            target_data_url = encode_image_to_data_url(image_source)
            messages = self._build_prompt_messages(
                target_data_url=target_data_url,
                approved_assets=approved_assets,
                context_note=context_note,
            )

            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
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
            return self.deterministic_fallback(image_source, approved_assets)