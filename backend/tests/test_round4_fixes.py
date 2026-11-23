"""Regression tests for the round-4 fixes.

Covers: schema upgrade of an existing database, image OCR routing without temp-path leaks,
the brand-name check, merged explanations without repeats, and the server-side `editable` flag.
"""
import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from sqlalchemy import create_engine, inspect


def test_add_missing_columns_upgrades_an_old_knowledge_table(tmp_path):
    from app.database import add_missing_columns

    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE knowledge_entries (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
        "entry_type TEXT NOT NULL, title TEXT NOT NULL, rule_text TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{db_file}")
    added = add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("knowledge_entries")}
    assert {"asset_type", "image_path", "usage_rules"} <= columns
    assert "knowledge_entries.asset_type" in added
    assert add_missing_columns(engine) == []  # idempotent on a second run


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_image_units_never_expose_the_temp_path():
    from app.services import orchestrator

    units, temp_paths = orchestrator._extract_with_image_support(
        text=None, file_name="ad.png", file_bytes=_png_bytes()
    )
    temp_path = temp_paths[0] if temp_paths else None
    try:
        assert temp_path and Path(temp_path).is_file()
        image_units = [u for u in units if u.unit_type == "image"]
        assert image_units, "image uploads must produce an image unit"
        for unit in image_units:
            assert temp_path not in unit.content
            assert unit.metadata.image_path == temp_path
            assert unit.metadata.source_name == "ad.png"
        assert all(temp_path not in (u.content or "") for u in units)
    finally:
        if temp_path and Path(temp_path).exists():
            Path(temp_path).unlink()


def test_brand_check_flags_another_organisation_only():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_brand_name

    other = ContentUnit(
        unit_id="u1", unit_type="text", content="Brought to you by DreamTech Organisation today."
    )
    own = ContentUnit(unit_id="u2", unit_type="text", content="NovaTech Cloud Inc. protects your data.")

    flagged = _check_brand_name(other, "NovaTech Cloud Workspace")
    assert len(flagged) == 1
    assert flagged[0].category == "brand"
    assert flagged[0].original_span == "DreamTech Organisation"
    assert flagged[0].original_span in other.content
    assert _check_brand_name(own, "NovaTech Cloud Workspace") == []


def _banned_term(title, severity="major"):
    return SimpleNamespace(
        id=f"term-{title}",
        title=title,
        rule_text=f"Do not use {title}.",
        owner=None,
        expiry_date=None,
        severity=severity,
    )


def test_banned_term_matches_guaranteed_case_and_spacing_variants():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_banned_terms

    for index, content in enumerate(("100% Guaranteed!", "100%GUARANTEED")):
        unit = ContentUnit(unit_id=f"guaranteed-{index}", unit_type="text", content=content)
        findings = _check_banned_terms(unit, [_banned_term("guaranteed")])

        assert len(findings) == 1
        assert findings[0].original_span in unit.content
        assert findings[0].original_span.casefold() == "guaranteed"


def test_banned_term_matches_joined_free_iphone_text():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_banned_terms

    for index, content in enumerate(("Get Free iphone 16!", "FREEiPHONE")):
        unit = ContentUnit(unit_id=f"free-iphone-{index}", unit_type="text", content=content)
        findings = _check_banned_terms(unit, [_banned_term("free iphone")])

        assert len(findings) == 1
        assert findings[0].original_span in unit.content


def test_banned_term_does_not_match_inside_longer_word():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_banned_terms

    unit = ContentUnit(unit_id="freedom", unit_type="text", content="Choose freedom today.")

    assert _check_banned_terms(unit, [_banned_term("free")]) == []


def test_brand_check_drops_single_letter_logo_fragment():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_brand_name

    unit = ContentUnit(
        unit_id="u-logo-fragment",
        unit_type="text",
        content="D DreamTech Organisation",
    )

    findings = _check_brand_name(unit, "NovaTech Cloud Workspace")

    assert len(findings) == 1
    assert findings[0].original_span == "DreamTech Organisation"
    assert findings[0].original_span in unit.content
    assert _check_brand_name(
        ContentUnit(unit_id="u-own", unit_type="text", content="NovaTech Cloud Inc."),
        "NovaTech Cloud Workspace",
    ) == []


def test_brand_check_flags_camel_case_brand_in_marketing_headline():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_brand_name

    unit = ContentUnit(
        unit_id="u-camel",
        unit_type="text",
        content="DreamTech: Get 100% Free iPhone 16!\nLimited Time\nClaim your offer now.",
    )

    findings = _check_brand_name(unit, "NovaTech Cloud Workspace")

    assert len(findings) == 1
    assert findings[0].category == "brand"
    assert findings[0].severity == "major"
    assert findings[0].original_span == "DreamTech"
    assert findings[0].original_span in unit.content
    assert findings[0].explanation == (
        "The content names 'DreamTech', which does not match this workspace's brand "
        "'NovaTech Cloud Workspace'. Confirm the material belongs to this brand before publishing."
    )


def test_brand_check_ignores_ordinary_marketing_words():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_brand_name

    unit = ContentUnit(
        unit_id="u-words",
        unit_type="text",
        content="Get Free Money Too! Click Now to Claim Your Daily Winner.",
    )

    assert _check_brand_name(unit, "NovaTech Cloud Workspace") == []


def test_rapidocr_text_joins_detections_in_reading_order(monkeypatch, caplog):
    from app.services import extraction

    caplog.set_level("INFO", logger=extraction.__name__)
    detections = [
        [[[100, 50], [180, 50], [180, 70], [100, 70]], "Second", 0.95],
        [[[100, 10], [180, 10], [180, 30], [100, 30]], "world", 0.92],
        [[[10, 10], [80, 10], [80, 30], [10, 30]], "Hello", 0.99],
    ]
    monkeypatch.setattr(
        extraction,
        "_get_rapidocr_engine",
        lambda: lambda image: (detections, 0.01),
    )

    assert extraction._rapidocr_text(Image.new("RGB", (200, 100))) == "Hello world\nSecond"
    assert "OCR engine=rapidocr lines=2" in caplog.text


def test_rapidocr_text_drops_entries_below_confidence_threshold(monkeypatch):
    from app.services import extraction

    detections = [
        [[[0, 0], [100, 0], [100, 20], [0, 20]], "Keep this", 0.5],
        [[[0, 30], [100, 30], [100, 50], [0, 50]], "Drop this", 0.49],
    ]
    monkeypatch.setattr(
        extraction,
        "_get_rapidocr_engine",
        lambda: lambda image: (detections, 0.01),
    )

    assert extraction._rapidocr_text(Image.new("RGB", (200, 100))) == "Keep this"


def test_ocr_image_uses_tesseract_when_rapidocr_returns_no_text(monkeypatch):
    from app.services import extraction

    called = []
    monkeypatch.setattr(extraction, "_get_rapidocr_engine", lambda: lambda image: None)
    monkeypatch.setattr(extraction, "_tesseract_text", lambda image: called.append(True) or "Fallback text")

    assert extraction._ocr_image(Image.new("RGB", (200, 100))) == "Fallback text"
    assert called == [True]


def test_rapidocr_real_engine_reads_generated_text_when_installed(monkeypatch):
    import pytest

    rapidocr = pytest.importorskip("rapidocr_onnxruntime")
    from PIL import ImageDraw, ImageFont
    from app.services import extraction

    image = Image.new("RGB", (900, 180), "white")
    font_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    font = next(
        (ImageFont.truetype(path, 72) for path in font_paths if Path(path).is_file()),
        ImageFont.load_default(),
    )
    ImageDraw.Draw(image).text((24, 35), "RAPID OCR TEST", fill="black", font=font)
    monkeypatch.setattr(extraction, "RapidOCR", rapidocr.RapidOCR)
    monkeypatch.setattr(extraction, "_rapidocr_checked", False)
    monkeypatch.setattr(extraction, "_rapidocr_engine", None)

    assert extraction._rapidocr_text(image)


def test_vision_fallback_skips_lockup_comparisons_for_marketing_image(tmp_path):
    from app.services.vision_verifier import ApprovedAssetSpec, VisionVerifier

    reference = _png_bytes()
    reference_path = tmp_path / "approved_logo.png"
    reference_path.write_bytes(reference)
    assets = [
        ApprovedAssetSpec(
            asset_id="logo-ref",
            name="Primary Wordmark",
            asset_type="logo",
            file_path=str(reference_path),
            brand_colors=["#0F172A"],
        )
    ]
    marketing_image = tmp_path / "campaign_ad.png"
    Image.new("RGB", (1200, 628), (255, 240, 0)).save(marketing_image)
    verifier = VisionVerifier(api_key=None)

    ordinary_result = verifier.deterministic_fallback(marketing_image, assets)
    lockup_result = verifier.deterministic_fallback(
        marketing_image, assets, target_metadata={"asset_type": "logo"}
    )

    assert not any("histogram distance" in f.explanation for f in ordinary_result.findings)
    assert not any("Aspect ratio" in f.explanation for f in ordinary_result.findings)
    assert any("palette" in f.explanation for f in ordinary_result.findings)
    assert any("histogram distance" in f.explanation for f in lockup_result.findings)


def test_merged_explanations_drop_repeated_rule_text():
    from app.services.orchestrator import _merge_explanations

    merged = _merge_explanations(
        [
            "'guaranteed' is listed as a banned term. Do not use 'guaranteed' for outcomes unless Legal approves.",
            "'guaranteed' conflicts with approved terminology: Do not use 'guaranteed' for outcomes "
            "unless Legal approves. Remove the banned term.",
        ]
    )
    assert merged.count("Do not use 'guaranteed' for outcomes unless Legal approves") == 1
    assert "Remove the banned term." in merged


def test_editable_is_true_only_for_text_spans_still_in_the_draft():
    from app.services.persistence import finding_to_schema

    base = dict(
        id=1, unit_id="u1", severity="major", explanation="x", evidence_id=None,
        evidence_title=None, evidence_quote=None, suggested_edit="fix", confidence=0.9,
        status="open", dispute_reason=None,
    )
    draft = "We guarantee 100% uptime."
    text_rec = SimpleNamespace(category="fact", original_span="100% uptime", **base)
    visual_rec = SimpleNamespace(category="visual", original_span="[Image: ad.png]", **base)
    gone_rec = SimpleNamespace(category="fact", original_span="zero downtime", **base)

    assert finding_to_schema(None, text_rec, draft).editable is True
    assert finding_to_schema(None, visual_rec, draft).editable is False
    assert finding_to_schema(None, gone_rec, draft).editable is False
