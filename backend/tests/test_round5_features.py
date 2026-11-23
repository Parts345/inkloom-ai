"""Regression tests for round-5 features.

Covers: dispute reason clustering, visual rules in the VLM prompt (and not in the pixel fallback),
embedded-image extraction from DOCX, layout-aware scanned-page OCR blocks, and is_ocr marking.
"""
import io
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image


def _png_bytes(size=(200, 120), color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Feature 1: dispute reason clustering
# ---------------------------------------------------------------------------


def test_similar_dispute_reasons_are_grouped_and_unrelated_ones_are_not():
    from app.services.dispute_refiner import cluster_dispute_reasons

    clusters = cluster_dispute_reasons(
        [
            "quote from customer review",
            "customer review quote is accurate",
            "internal planning note only",
            "internal note not public",
        ]
    )
    assert len(clusters) == 2
    assert [c.count for c in clusters] == [2, 2]
    assert all(c.samples for c in clusters)


def test_blank_dispute_reasons_produce_no_clusters():
    from app.services.dispute_refiner import cluster_dispute_reasons

    assert cluster_dispute_reasons(["", "   "]) == []


def test_fallback_summary_mentions_distinct_themes(monkeypatch):
    from app.services.dispute_refiner import CreatorDisputeRecord, DisputeRefiner, RuleEntryInput

    for var in ("LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    refiner = DisputeRefiner(api_key=None, client=None)
    rule = RuleEntryInput(rule_id="r1", title="Competitor mentions", description="No competitor names.")
    disputes = [
        CreatorDisputeRecord(dispute_reason="quote from customer review", flagged_text="Acme"),
        CreatorDisputeRecord(dispute_reason="internal planning note only", flagged_text="Acme"),
    ]
    result = refiner.deterministic_fallback(rule, disputes)
    assert "Distinct themes:" in result.cluster_summary


# ---------------------------------------------------------------------------
# Feature 2: visual rules and embedded images
# ---------------------------------------------------------------------------


def _visual_rule_spec(asset_id="rule-no-competitors"):
    from app.services.vision_verifier import ApprovedAssetSpec

    return ApprovedAssetSpec(
        asset_id=asset_id,
        name="No competitor logos",
        asset_type="visual_rule",
        file_path=None,
        description="Never show competitor logos in campaign imagery.",
    )


def test_vlm_prompt_asks_for_style_and_prohibited_elements_and_lists_visual_rules():
    from app.services.vision_verifier import VisionVerifier

    verifier = VisionVerifier(client=None, api_key="unused")
    messages = verifier._build_prompt_messages(
        target_data_url="data:image/png;base64,AAAA",
        approved_assets=[_visual_rule_spec()],
    )
    system_text = messages[0]["content"]
    user_blocks = messages[1]["content"]
    user_text = " ".join(b.get("text", "") for b in user_blocks if b.get("type") == "text")

    assert "Style adherence" in system_text
    assert "Prohibited visual elements" in system_text
    assert "Misleading promotional visuals" in system_text
    assert "rule-no-competitors" in user_text


def test_pixel_fallback_never_uses_visual_rules_as_evidence(monkeypatch):
    from app.services.vision_verifier import VisionVerifier

    verifier = VisionVerifier(api_key="unused")
    monkeypatch.setattr(verifier, "_get_client", lambda: None)

    response = verifier.verify(_png_bytes(), [_visual_rule_spec()])
    assert all(f.evidence_id != "rule-no-competitors" for f in response.findings)


def _docx_bytes_with_media(media_name: str, media: bytes) -> bytes:
    import docx
    doc = docx.Document()
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    out = io.BytesIO()
    with zipfile.ZipFile(buf, "r") as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(media_name, media)
    return out.getvalue()


def test_docx_embedded_images_are_listed_and_non_images_skipped():
    from app.services import extraction

    png = _png_bytes()
    docx = _docx_bytes_with_media("word/media/image1.png", png)
    found = extraction.extract_embedded_images(docx, "brochure.docx")
    assert found == [(png, "image1.png", None)]


def test_corrupt_docx_returns_no_embedded_images():
    from app.services import extraction

    assert extraction.extract_embedded_images(b"not a zip", "broken.docx") == []


# ---------------------------------------------------------------------------
# Feature 3: layout-aware scanned-page OCR
# ---------------------------------------------------------------------------


def test_ocr_entries_become_headings_and_paragraphs():
    from app.services.extraction import _group_ocr_entries

    entries = [
        (10.0, 0.0, 20.0, "DREAMTECH"),
        (50.0, 0.0, 40.0, "Get 100% Free"),
        (100.0, 0.0, 12.0, "Body line one"),
        (116.0, 0.0, 12.0, "body line two"),
        (200.0, 0.0, 12.0, "Second para"),
    ]
    blocks = _group_ocr_entries(entries)
    assert blocks == [
        ("DREAMTECH", True),
        ("Get 100% Free", True),
        ("Body line one body line two", False),
        ("Second para", False),
    ]


def test_empty_ocr_output_gives_no_blocks():
    from app.services.extraction import _group_ocr_entries

    assert _group_ocr_entries([]) == []


def test_ocr_units_are_marked_is_ocr():
    from app.services.extraction import _create_text_unit

    assert _create_text_unit("Scanned line", page=1, extraction_method="ocr").metadata.is_ocr is True
    assert _create_text_unit("Digital line", page=1, extraction_method="digital").metadata.is_ocr is False


def test_ocr_drops_single_character_lines_but_keeps_numbers_and_short_words(monkeypatch):
    from app.services import extraction

    detections = [
        [[[0, 0], [20, 0], [20, 10], [0, 10]], "D", 0.99],
        [[[0, 20], [20, 20], [20, 30], [0, 30]], "16", 0.99],
        [[[0, 40], [20, 40], [20, 50], [0, 50]], "to", 0.99],
        [[[24, 40], [40, 40], [40, 50], [24, 50]], "a", 0.99],
        [[[44, 40], [86, 40], [86, 50], [44, 50]], "place", 0.99],
    ]
    monkeypatch.setattr(
        extraction,
        "_get_rapidocr_engine",
        lambda: lambda image: (detections, 0.01),
    )

    image = Image.new("RGB", (100, 60))
    assert extraction._rapidocr_text(image) == "16\nto a place"
    assert [entry[3] for entry in extraction._rapidocr_entries(image)] == [
        "16", "to", "a", "place"
    ]
    assert extraction._split_paragraphs("D\n\n16\nto a phrase") == ["16\nto a phrase"]


def test_ocr_drops_narrow_logo_letter_but_keeps_article_a(monkeypatch):
    from app.services import extraction

    detections = [
        [[[0, 0], [6, 0], [6, 12], [0, 12]], "D", 0.99],
        [[[10, 0], [90, 0], [90, 12], [10, 12]], "DreamTech", 0.99],
        [[[0, 30], [20, 30], [20, 42], [0, 42]], "A", 0.99],
        [[[25, 30], [55, 30], [55, 42], [25, 42]], "new", 0.99],
        [[[60, 30], [90, 30], [90, 42], [60, 42]], "plan", 0.99],
    ]
    monkeypatch.setattr(
        extraction,
        "_get_rapidocr_engine",
        lambda: lambda image: (detections, 0.01),
    )

    entries = extraction._rapidocr_entries(Image.new("RGB", (100, 60)))

    assert [entry[3] for entry in entries] == ["DreamTech", "A", "new", "plan"]


def _finding(finding_id, category, span, severity="minor", explanation=None, suggested_edit=None):
    from app.schemas import Finding

    return Finding(
        finding_id=finding_id,
        unit_id="unit-1",
        category=category,
        severity=severity,
        original_span=span,
        explanation=explanation or f"Explanation for {finding_id}.",
        suggested_edit=suggested_edit,
        confidence=0.8,
    )


def test_overlapping_same_category_findings_merge_into_longest_span():
    from app.services.orchestrator import merge_findings

    merged = merge_findings([
        _finding("short", "terminology", "DreamTech", severity="minor"),
        _finding("long", "terminology", "D DreamTech", severity="critical", suggested_edit="DreamTech"),
    ])

    assert len(merged) == 1
    assert merged[0].original_span == "D DreamTech"
    assert merged[0].suggested_edit == "DreamTech"
    assert merged[0].severity == "critical"


def test_unrelated_categories_with_partial_span_overlap_stay_separate():
    from app.services.orchestrator import merge_findings

    merged = merge_findings([
        _finding("fact", "fact", "DreamTech Organisation"),
        _finding("term", "terminology", "DreamTech"),
    ])

    assert len(merged) == 2
    assert {finding.category for finding in merged} == {"fact", "terminology"}


def test_visual_finding_is_not_merged_with_text_finding():
    from app.services.orchestrator import merge_findings

    merged = merge_findings([
        _finding("visual", "visual", "DreamTech"),
        _finding("term", "terminology", "DreamTech"),
    ])

    assert len(merged) == 2


def test_merged_findings_keep_highest_severity():
    from app.services.orchestrator import merge_findings

    merged = merge_findings([
        _finding("minor", "terminology", "DreamTech", severity="minor"),
        _finding("major", "terminology", "D DreamTech", severity="major"),
    ])

    assert len(merged) == 1
    assert merged[0].severity == "major"


def test_brand_and_terminology_overlap_merge_uses_brand_and_term_edit():
    from app.services.orchestrator import merge_findings

    brand = _finding("brand", "brand", "DreamTech", severity="minor")
    terminology = _finding(
        "term",
        "terminology",
        "DreamTech",
        severity="critical",
        suggested_edit="NovaTech Cloud",
    )

    merged = merge_findings([brand, terminology])

    assert len(merged) == 1
    assert merged[0].category == "brand"
    assert merged[0].severity == "critical"
    assert merged[0].suggested_edit == "NovaTech Cloud"
    assert merged[0].explanation.startswith(brand.explanation)
    assert "NovaTech Cloud" in merged[0].explanation


def test_exclamation_tone_findings_are_aggregated_per_unit():
    from app.services.verification import ContentUnit, IssueCategory, _deterministic_mock_analyze

    unit = ContentUnit(
        unit_id="tone-lines",
        workspace_id="ws_test",
        text="First line!\nSecond line!!!\nThird line!",
    )
    findings = _deterministic_mock_analyze(unit, [])
    tone_findings = [finding for finding in findings if finding.category == IssueCategory.TONE]

    assert len(tone_findings) == 1
    assert tone_findings[0].original_span == "First line!"
    assert "3 lines" in tone_findings[0].explanation


def test_full_width_exclamation_is_detected_with_exact_source_span():
    from app.services.verification import ContentUnit, IssueCategory, _deterministic_mock_analyze

    unit = ContentUnit(
        unit_id="tone-full-width",
        workspace_id="ws_test",
        text="Earn1,00,000Daily！",
    )
    findings = _deterministic_mock_analyze(unit, [])
    tone_findings = [finding for finding in findings if finding.category == IssueCategory.TONE]

    assert len(tone_findings) == 1
    assert tone_findings[0].original_span == unit.text
    assert tone_findings[0].original_span in unit.text


# ---------------------------------------------------------------------------
# Embedded image units keep their temp path out of the draft text
# ---------------------------------------------------------------------------


def test_embedded_image_units_hide_temp_paths_and_skip_alt_text_check():
    from app.services import orchestrator
    from app.services.deterministic import _check_image_alt_text

    docx = _docx_bytes_with_media("word/media/logo.png", _png_bytes())
    units, temp_paths = orchestrator._extract_with_image_support(
        text=None, file_name="brochure.docx", file_bytes=docx
    )
    try:
        image_units = [u for u in units if u.unit_type == "image"]
        assert len(image_units) == 1
        unit = image_units[0]
        assert unit.metadata.embedded is True
        assert unit.metadata.image_path in temp_paths
        assert unit.metadata.image_path not in unit.content
        assert _check_image_alt_text(unit) == []
    finally:
        import os

        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)


# ---------------------------------------------------------------------------
# Feature tests for Round 5 fixes (1.1 - 1.6)
# ---------------------------------------------------------------------------


def test_simulated_timeout_produces_audit_event_and_flag(monkeypatch):
    """LLM timeout: llm_verification_failed flag is set, audit event recorded, det findings kept."""
    import asyncio
    from app.schemas import ContentUnit, Finding, SubmissionResponse, UnitMetadata
    from app.services import deterministic, extraction, orchestrator, persistence, verification

    # Fixed unit so the det_finding unit_id matches what extraction returns.
    fixed_unit = ContentUnit(
        unit_id="u1",
        unit_type="text",
        content="This is guaranteed text.",
        metadata=UnitMetadata(),
    )

    monkeypatch.setattr(
        extraction,
        "extract_units",
        lambda **kw: [fixed_unit],
    )

    det_finding = Finding(
        finding_id="det_1",
        unit_id="u1",
        category="terminology",
        severity="critical",
        original_span="guaranteed",
        explanation="guaranteed is banned",
        confidence=1.0,
    )
    monkeypatch.setattr(
        deterministic,
        "run_checks",
        lambda units, ws: [det_finding],
    )

    async def timeout_verify(units, ws):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(verification, "verify_units", timeout_verify)

    # Mock persistence so the test does not need a real database.
    captured: dict = {}

    def mock_save_analysis(
        *,
        submission_id,
        workspace_id,
        title,
        actor_id,
        status,
        current_text,
        units,
        findings,
        audit_events,
        source_format="text",
        llm_verification_failed=False,
    ):
        captured["llm_verification_failed"] = llm_verification_failed
        captured["audit_events"] = audit_events
        captured["findings"] = findings
        return SubmissionResponse(
            submission_id=submission_id,
            title=title,
            status=status,
            version=1,
            current_text=current_text,
            findings=findings,
            audit_log=audit_events,
            llm_verification_failed=llm_verification_failed,
        )

    monkeypatch.setattr(persistence, "save_analysis", mock_save_analysis)

    resp = asyncio.run(orchestrator.analyse_submission(
        workspace_id="test_ws",
        actor_id="test_actor",
        title="Timeout Test",
        text="This is guaranteed text.",
    ))

    assert resp.llm_verification_failed is True
    audit_actions = [a.action for a in resp.audit_log]
    assert "llm_verification_failed" in audit_actions
    failed_event = next(a for a in resp.audit_log if a.action == "llm_verification_failed")
    assert "timeout" in (failed_event.detail or "")
    # Deterministic finding must survive even when LLM times out.
    assert any(f.original_span == "guaranteed" for f in resp.findings)


def test_merge_duplicate_guaranteed_findings():
    from app.schemas import Finding
    from app.services.orchestrator import merge_findings

    f1 = Finding(
        finding_id="f1",
        unit_id="u1",
        category="terminology",
        severity="critical",
        original_span="Guaranteed",
        explanation="'Guaranteed' is listed as a banned term for this workspace.",
        confidence=1.0,
    )
    f2 = Finding(
        finding_id="f2",
        unit_id="u1",
        category="terminology",
        severity="critical",
        original_span="Guaranteed!",
        explanation="The term 'guaranteed' is banned for performance or outcome claims. It must be removed.",
        confidence=1.0,
    )

    merged = merge_findings([f1, f2])
    assert len(merged) == 1
    assert merged[0].original_span == "Guaranteed!"
    assert merged[0].severity == "critical"
    assert "'Guaranteed' is listed as a banned term" in merged[0].explanation


def test_merge_duplicate_findings_cross_unit_identical_text():
    from app.schemas import Finding
    from app.services.orchestrator import merge_findings

    f1 = Finding(
        finding_id="f1",
        unit_id="u1",
        category="terminology",
        severity="critical",
        original_span="Guaranteed",
        explanation="Explanation 1.",
        confidence=1.0,
    )
    f2 = Finding(
        finding_id="f2",
        unit_id="u2",
        category="terminology",
        severity="critical",
        original_span="Guaranteed!",
        explanation="Explanation 2.",
        confidence=1.0,
    )

    unit_texts = {
        "u1": "100% Guaranteed! Call today.",
        "u2": "100% Guaranteed! Call today.",
    }
    merged = merge_findings([f1, f2], units=unit_texts)
    assert len(merged) == 1
    assert merged[0].original_span == "Guaranteed!"


def test_normalise_for_checks_full_width_punctuation():
    from app.models import KnowledgeEntry
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_banned_terms, _check_tone, normalise_for_checks

    text = "Earn1,00,000Daily！"
    norm = normalise_for_checks(text)
    assert norm == "Earn1,00,000Daily!"

    unit = ContentUnit(
        unit_id="u_fullwidth",
        content=text,
        unit_type="text",
    )

    tone_findings = _check_tone(unit)
    assert len(tone_findings) == 1
    assert tone_findings[0].original_span == text
    assert tone_findings[0].original_span in unit.content

    unit2 = ContentUnit(
        unit_id="u_banned",
        content="100% Guaranteed！",
        unit_type="text",
    )
    entry = KnowledgeEntry(
        id="kb-guaranteed",
        workspace_id="ws1",
        entry_type="banned_term",
        title="Guaranteed",
        rule_text="Do not use guaranteed.",
        severity="critical",
    )
    banned_findings = _check_banned_terms(unit2, [entry])
    assert len(banned_findings) == 1
    assert banned_findings[0].original_span == "Guaranteed"
    assert banned_findings[0].original_span in unit2.content


def test_three_exclamation_mark_lines_produce_one_tone_finding():
    from app.schemas import ContentUnit
    from app.services.deterministic import _check_tone

    unit = ContentUnit(
        unit_id="u_lines",
        content="First line!\nSecond line!!!\nThird line!\nFourth line without.",
        unit_type="text",
    )
    findings = _check_tone(unit)
    assert len(findings) == 1
    assert findings[0].category == "tone"
    assert findings[0].severity == "minor"
    assert findings[0].original_span == "First line!"
    assert "3 lines" in findings[0].explanation


def test_llm_prompt_better_rewrites_instructions():
    from app.services.verification import SYSTEM_PROMPT

    prompt_lower = SYSTEM_PROMPT.lower()
    assert "keep the original capitalisation of the span" in prompt_lower
    assert "do not replace a claim with a shorter claim that keeps the same offer" in prompt_lower
    assert "if the only fix removes the offer, return an empty suggested_edit and say in the explanation that the author must rewrite the sentence" in prompt_lower


# ---------------------------------------------------------------------------
# Vision provider split
# ---------------------------------------------------------------------------


def test_vlm_settings_use_own_provider_and_never_the_text_key():
    from app.services.vision_verifier import resolve_vlm_settings

    key, base, model = resolve_vlm_settings(
        {"VLM_PROVIDER": "gemini", "VLM_API_KEY": "vision-key", "LLM_API_KEY": "text-key"}
    )
    assert key == "vision-key"
    assert base == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert model == "gemini-2.5-flash"


def test_vision_provider_without_its_own_key_does_not_borrow_the_text_key():
    from app.services.vision_verifier import resolve_vlm_settings

    key, base, _model = resolve_vlm_settings({"VLM_PROVIDER": "gemini", "LLM_API_KEY": "text-key"})
    assert key is None
    assert base is not None


def test_no_vlm_settings_returns_nothing_so_llm_settings_apply():
    from app.services.vision_verifier import resolve_vlm_settings

    assert resolve_vlm_settings({"LLM_API_KEY": "text-key"}) == (None, None, None)


# ---------------------------------------------------------------------------
# Silent LLM failure must be recorded
# ---------------------------------------------------------------------------


def test_configured_llm_failure_is_recorded_not_silent(monkeypatch):
    from app.services import verification

    class ConfiguredClient:
        is_configured = True

    def failing_llm(*args, **kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(verification, "LLMProviderClient", lambda: ConfiguredClient())
    monkeypatch.setattr(verification, "_llm_analyze", failing_llm)

    unit = verification.ContentUnit(unit_id="u1", workspace_id="ws", text="Get Free iPhone today.")
    failures: list = []
    findings = verification.analyze_text_unit(unit, [], evidence_pool=[], failures=failures)

    assert failures == ["u1"]
    assert isinstance(findings, list)
