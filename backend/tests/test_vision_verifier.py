"""
Unit tests for Improvement 2: Multimodal Vision Asset Verification (`vision_verifier.py`).
Covers base64 encoding, mocked VLM structured JSON responses, invalid evidence ID normalization,
and offline deterministic OpenCV/Pillow fallback behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services.vision_verifier import (
    ApprovedAssetSpec,
    VisionVerifier,
    encode_image_to_data_url,
)


def _create_solid_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    img = Image.new("RGB", size, color=color)
    img.save(path, format="PNG")
    return path


def _mock_openai_response(json_payload: dict) -> MagicMock:
    mock_client = MagicMock()
    message = SimpleNamespace(content=json.dumps(json_payload))
    choice = SimpleNamespace(message=message)
    mock_client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
    return mock_client


@pytest.fixture()
def sample_assets(tmp_path: Path) -> list[ApprovedAssetSpec]:
    ref_logo = _create_solid_image(tmp_path / "approved_logo.png", (800, 200), (15, 23, 42))
    return [
        ApprovedAssetSpec(
            asset_id="asset-logo-primary",
            name="NovaTech Primary Wordmark",
            asset_type="logo",
            file_path=str(ref_logo),
            description="Dark navy horizontal wordmark on light backgrounds",
            brand_colors=["#0F172A", "#3B82F6"],
            placement_rules="Top-left corner with minimum 24px clear space",
        ),
        ApprovedAssetSpec(
            asset_id="rule-visual-palette",
            name="Core Brand Palette",
            asset_type="palette_rule",
            description="Primary slate and electric blue brand palette",
            brand_colors=["#0F172A", "#3B82F6"],
        ),
    ]


def test_encode_image_to_data_url(tmp_path: Path) -> None:
    img_path = _create_solid_image(tmp_path / "sample.png", (100, 100), (59, 130, 246))
    data_url = encode_image_to_data_url(img_path)

    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 50


def test_vlm_structured_json_response(
    tmp_path: Path, sample_assets: list[ApprovedAssetSpec]
) -> None:
    target_img = _create_solid_image(tmp_path / "campaign_banner.png", (1200, 628), (220, 38, 38))

    mock_payload = {
        "findings": [
            {
                "category": "visual",
                "severity": "critical",
                "confidence": 0.94,
                "explanation": "Campaign banner uses an outdated monochrome emblem instead of the approved wordmark.",
                "evidence_id": "asset-logo-primary",
                "suggested_edit": "Replace outdated monochrome logo with approved primary NovaTech Cloud wordmark.",
            }
        ]
    }
    mock_client = _mock_openai_response(mock_payload)

    verifier = VisionVerifier(
        api_key="test-key",
        model="qwen/qwen3.8-27b",
        client=mock_client,
    )
    result = verifier.verify(target_img, sample_assets, context_note="Q4 Cloud Launch Hero")

    assert result.used_fallback is False
    assert result.model_used == "qwen/qwen3.8-27b"
    assert len(result.findings) == 1

    finding = result.findings[0]
    assert finding.category == "visual"
    assert finding.severity == "critical"
    assert finding.confidence == pytest.approx(0.94)
    assert finding.evidence_id == "asset-logo-primary"
    assert "NovaTech" in finding.suggested_edit

    # Ensure multimodal payload sent both target and reference image_url blocks
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    user_blocks = call_kwargs["messages"][1]["content"]
    image_blocks = [b for b in user_blocks if b["type"] == "image_url"]
    assert len(image_blocks) == 2


def test_vlm_normalizes_hallucinated_evidence_id(
    tmp_path: Path, sample_assets: list[ApprovedAssetSpec]
) -> None:
    target_img = _create_solid_image(tmp_path / "campaign.png", (800, 200), (15, 23, 42))
    mock_payload = {
        "findings": [
            {
                "category": "visual",
                "severity": "minor",
                "confidence": 0.72,
                "explanation": "Logo clear space slightly tight near top border.",
                "evidence_id": "non-existent-id-999",
                "suggested_edit": "Add 24px padding around the wordmark.",
            }
        ]
    }
    verifier = VisionVerifier(api_key="test-key", client=_mock_openai_response(mock_payload))
    result = verifier.verify(target_img, sample_assets)

    assert len(result.findings) == 1
    assert result.findings[0].evidence_id in {"asset-logo-primary", "rule-visual-palette"}


def test_offline_deterministic_fallback_when_no_api_key(
    tmp_path: Path, sample_assets: list[ApprovedAssetSpec], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # Low-res, off-palette (bright yellow), wrong aspect ratio image
    bad_img = _create_solid_image(tmp_path / "bad_upload.png", (200, 200), (255, 240, 0))

    verifier = VisionVerifier(api_key=None)
    result = verifier.verify(bad_img, sample_assets)

    assert result.used_fallback is True
    assert result.model_used == "deterministic-opencv-pillow"
    assert len(result.findings) >= 2
    assert all(f.category == "visual" for f in result.findings)
    assert all(0.0 <= f.confidence <= 1.0 for f in result.findings)


def test_fallback_on_vlm_timeout(
    tmp_path: Path, sample_assets: list[ApprovedAssetSpec]
) -> None:
    target_img = _create_solid_image(tmp_path / "small_offbrand.png", (250, 250), (255, 0, 128))
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("VLM request timed out")

    verifier = VisionVerifier(api_key="test-key", client=mock_client)
    result = verifier.verify(target_img, sample_assets)

    assert result.used_fallback is True
    assert len(result.findings) >= 1