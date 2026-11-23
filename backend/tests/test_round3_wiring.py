"""Round 3 wiring: the entry points the orchestrator and analytics router call.

No network, no database, no SentenceTransformer.
"""
import asyncio

from PIL import Image

from app.services import dispute_refiner, vision_verifier


class _Metadata:
    def __init__(self, image_path):
        self.image_path = image_path


class _Unit:
    def __init__(self, unit_id, image_path):
        self.unit_id = unit_id
        self.content = image_path
        self.metadata = _Metadata(image_path)


def _png(tmp_path, name="campaign.png"):
    path = tmp_path / name
    Image.new("RGB", (64, 64), (200, 30, 30)).save(path)
    return str(path)


def test_verify_images_returns_nothing_without_approved_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(vision_verifier, "_load_approved_assets", lambda ws: [])
    assert vision_verifier.verify_images([_Unit("img_1", _png(tmp_path))], "ws_test") == []


def test_verify_images_maps_vlm_findings_to_shared_visual_findings(monkeypatch, tmp_path):
    asset = vision_verifier.ApprovedAssetSpec(
        asset_id="asset-1",
        name="Primary Logo",
        asset_type="logo",
        description="Logo must sit on a white background.",
    )
    monkeypatch.setattr(vision_verifier, "_load_approved_assets", lambda ws: [asset])

    canned = vision_verifier.VisionVerificationResponse(
        findings=[
            vision_verifier.VisualFinding(
                severity="major",
                confidence=0.9,
                explanation="Logo is placed on a yellow background, not white.",
                evidence_id="asset-1",
                suggested_edit="Place the approved logo on a white background.",
            )
        ],
        used_fallback=False,
        model_used="test-model",
    )
    monkeypatch.setattr(
        vision_verifier.VisionVerifier,
        "verify",
        lambda self, image_source, approved_assets, context_note=None: canned,
    )

    findings = vision_verifier.verify_images([_Unit("img_1", _png(tmp_path))], "ws_test")

    assert len(findings) == 1
    assert findings[0].category == "visual"
    assert findings[0].severity == "major"
    assert findings[0].unit_id == "img_1"
    assert findings[0].evidence.evidence_id == "asset-1"
    assert findings[0].evidence.source_type == "asset"


def test_verify_images_skips_missing_image_files(monkeypatch, tmp_path):
    asset = vision_verifier.ApprovedAssetSpec(asset_id="asset-1", name="Logo", asset_type="logo")
    monkeypatch.setattr(vision_verifier, "_load_approved_assets", lambda ws: [asset])
    assert vision_verifier.verify_images([_Unit("img_1", str(tmp_path / "gone.png"))], "ws_test") == []


def test_refine_disputed_rule_falls_back_without_llm_key(monkeypatch):
    for var in ("LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    result = asyncio.run(
        dispute_refiner.refine_disputed_rule(
            rule_id="kb-t03",
            rule_title="guaranteed",
            rule_text="Do not use 'guaranteed' for performance or outcomes unless Legal has approved it.",
            disputes=[
                {
                    "dispute_reason": "Our SLA guarantee was approved by Legal",
                    "original_span": "guaranteed",
                    "submission_title": "Q4 launch",
                }
            ],
        )
    )

    assert len(result["cluster_summary"]) >= 10
    assert len(result["proposed_rule_text"]) >= 10


def test_groq_provider_sets_groq_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert vision_verifier.VisionVerifier(api_key="k").base_url == "https://api.groq.com/openai/v1"
    assert dispute_refiner.DisputeRefiner(api_key="k").base_url == "https://api.groq.com/openai/v1"


def test_explicit_llm_base_url_wins_over_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    assert vision_verifier.VisionVerifier(api_key="k").base_url == "https://example.test/v1"
