"""Owner: Soham. Seeds the NovaTech Cloud demo workspace, knowledge base, and sample submissions idempotently."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import FindingRecord, KnowledgeEntry, Submission, Workspace, utcnow
from app.routers.demo import DEMO_WORKSPACE_ID
from app.services.persistence import add_audit

WORKSPACE_ID = DEMO_WORKSPACE_ID  # "ws_novatech_cloud"
WORKSPACE_NAME = "NovaTech Cloud"
PALETTE = ["#0F172A", "#0284C7", "#38BDF8"]

_TODAY = date.today()
_FUTURE = _TODAY + timedelta(days=365)
_EXPIRED = _TODAY - timedelta(days=45)


def _k(id_, type_, category, title, rule, severity, owner, source, expiry: Optional[date] = None):
    return dict(
        id=id_,
        entry_type=type_,
        category=category,
        title=title,
        rule_text=rule,
        severity=severity,
        owner=owner,
        source_doc=source,
        expiry_date=expiry,
    )


KNOWLEDGE = [
    # ---- 10 brand guidelines ----
    _k(
        "kb-g01", "guideline", "tone", "Friendly, confident, never hype",
        "Write in a warm, professional voice. Avoid hype, slang and exclamation marks in customer-facing copy.",
        "major", "Brand Team", "NovaTech Brand Voice Guide v4, section 2",
    ),
    _k(
        "kb-g02", "guideline", "tone", "Prefer active voice",
        "Use active voice. Say 'We encrypt your data', not 'Your data is encrypted by us'.",
        "minor", "Brand Team", "NovaTech Brand Voice Guide v4, section 3",
    ),
    _k(
        "kb-g03", "guideline", "style", "Sentence case headings",
        "Headings use sentence case ('Getting started'), never Title Case or ALL CAPS.",
        "minor", "Brand Team", "NovaTech Style Guide v2, section 1",
    ),
    _k(
        "kb-g04", "guideline", "style", "Use the Oxford comma",
        "In lists of three or more items, include the serial comma before 'and' or 'or'.",
        "minor", "Brand Team", "NovaTech Style Guide v2, section 2",
    ),
    _k(
        "kb-g05", "guideline", "terminology", "Product naming",
        "Write 'NovaTech Cloud' in full with this exact capitalization. Do not abbreviate to 'NTC' or 'Nova' in external content.",
        "major", "Brand Team", "NovaTech Style Guide v2, section 4",
    ),
    _k(
        "kb-g06", "guideline", "claims", "Claims need evidence",
        "Every performance, security or ranking claim must match an approved product fact. Unverifiable superlatives are not allowed.",
        "critical", "Legal", "NovaTech Marketing Claims Policy v3",
    ),
    _k(
        "kb-g07", "guideline", "accessibility", "Alt text required",
        "Every informative image needs concise alt text. Decorative images use empty alt text.",
        "major", "Design Team", "NovaTech Accessibility Standard v1, section 2",
    ),
    _k(
        "kb-g08", "guideline", "accessibility", "Minimum text contrast",
        "Text must meet WCAG AA contrast: 4.5:1 for body text and 3:1 for large text.",
        "major", "Design Team", "NovaTech Accessibility Standard v1, section 3",
    ),
    _k(
        "kb-g09", "guideline", "visual", "Brand palette only",
        "Use only the approved palette (#0F172A, #0284C7, #38BDF8) plus white and neutral greys.",
        "major", "Design Team", "NovaTech Visual Identity v5, section 1",
    ),
    _k(
        "kb-g10", "guideline", "style", "Spell out acronyms",
        "Spell out acronyms on first use, for example 'Service Level Agreement (SLA)'.",
        "minor", "Brand Team", "NovaTech Style Guide v2, section 5",
    ),
    # ---- 5 approved product facts ----
    _k(
        "kb-f01", "fact", "security", "SOC 2 Type II certified",
        "NovaTech Cloud is SOC 2 Type II certified. Always state the report type (Type II).",
        "critical", "Security Team", "SOC 2 Type II Report", _FUTURE,
    ),
    _k(
        "kb-f02", "fact", "reliability", "Uptime SLA is 99.95%",
        "The contractual monthly uptime SLA is 99.95%. Never claim 100% uptime or zero downtime.",
        "critical", "Platform Team", "Master Service Agreement, Schedule B", _FUTURE,
    ),
    _k(
        "kb-f03", "fact", "pricing", "Pricing tiers",
        "Starter is $29 per month, Business is $99 per month, and Enterprise is custom pricing.",
        "major", "Finance", "Pricing Sheet 2026", _FUTURE,
    ),
    _k(
        "kb-f04", "fact", "pricing", "Launch promotion: 3 months free on Business",
        "Launch promotion: the first 3 months of the Business tier were free. This promotion has ended.",
        "major", "Marketing Ops", "Launch Promo Brief", _EXPIRED,
    ),
    _k(
        "kb-f05", "fact", "infrastructure", "Data residency regions",
        "Customer data can be hosted in US-East, EU-West or AP-South.",
        "major", "Platform Team", "Infrastructure Overview 2026", _FUTURE,
    ),
    # ---- 5 preferred / banned terms ----
    _k(
        "kb-t01", "preferred_term", "terminology", "users -> customers",
        "Use 'customers' instead of 'users' in external copy.",
        "major", "Brand Team", "NovaTech Style Guide v2, section 6",
    ),
    _k(
        "kb-t02", "preferred_term", "terminology", "log in -> sign in",
        "Use 'sign in' instead of 'log in'.",
        "minor", "Brand Team", "NovaTech Style Guide v2, section 6",
    ),
    _k(
        "kb-t03", "banned_term", "terminology", "guaranteed",
        "Do not use 'guaranteed' for performance or outcomes unless Legal has approved that specific guarantee.",
        "critical", "Legal", "NovaTech Marketing Claims Policy v3",
    ),
    _k(
        "kb-t04", "banned_term", "terminology", "#1 in the world",
        "Ranking claims such as '#1 in the world' are unverified and banned.",
        "critical", "Legal", "NovaTech Marketing Claims Policy v3",
    ),
    _k(
        "kb-t05", "banned_term", "terminology", "military-grade encryption",
        "'Military-grade encryption' is a marketing term with no standard meaning. Describe the actual standard instead.",
        "major", "Security Team", "NovaTech Marketing Claims Policy v3",
    ),
]
KB = {row["id"]: row for row in KNOWLEDGE}

DRAFT_ID = "sub-demo-launch"
DRAFT_TEXT = (
    "Introducing NovaTech Cloud 3.0, the #1 in the world platform for modern engineering teams.\n\n"
    "Our users enjoy 100% uptime, guaranteed. Every plan is protected by military-grade encryption.\n\n"
    "Start today with 3 months free on the Business tier. "
    "Log in to your dashboard and see why teams love us!"
)

APPROVED_ID = "sub-demo-security"
APPROVED_ORIGINAL = (
    "NovaTech Cloud is SOC 2 Type II certified. Our users benefit from a 99.95% monthly uptime SLA, "
    "with data hosted in US-East, EU-West or AP-South."
)
APPROVED_CURRENT = APPROVED_ORIGINAL.replace("Our users", "Our customers")


def _finding(sub_id, unit_id, category, severity, span, explanation, kb_id, edit, conf, status="open"):
    kb = KB[kb_id]
    return FindingRecord(
        submission_id=sub_id,
        unit_id=unit_id,
        category=category,
        severity=severity,
        original_span=span,
        explanation=explanation,
        evidence_id=kb_id,
        evidence_title=kb["title"],
        evidence_quote=kb["rule_text"],
        suggested_edit=edit,
        confidence=conf,
        status=status,
    )


def _seed_review_draft(db: Session) -> None:
    if db.get(Submission, DRAFT_ID) is not None:
        return
    t0 = utcnow() - timedelta(minutes=12)
    db.add(
        Submission(
            id=DRAFT_ID,
            workspace_id=WORKSPACE_ID,
            title="NovaTech Cloud 3.0 launch email",
            source_format="text",
            original_content=DRAFT_TEXT,
            current_content=DRAFT_TEXT,
            version=1,
            status="in_review",
            created_by="Creator",
            created_at=t0,
        )
    )
    db.flush()
    db.add_all(
        [
            _finding(
                DRAFT_ID, "u1", "terminology", "critical", "the #1 in the world platform",
                "Unverified ranking claim. '#1 in the world' is a banned term.",
                "kb-t04", "a trusted platform", 0.98,
            ),
            _finding(
                DRAFT_ID, "u2", "fact", "critical", "100% uptime, guaranteed",
                "Contradicts the approved SLA (99.95%) and uses the banned word 'guaranteed'.",
                "kb-f02", "99.95% uptime backed by our SLA", 0.97,
            ),
            _finding(
                DRAFT_ID, "u2", "terminology", "major", "Our users",
                "Use 'customers' instead of 'users'.",
                "kb-t01", "Our customers", 0.95,
            ),
            _finding(
                DRAFT_ID, "u2", "terminology", "major", "military-grade encryption",
                "Banned marketing term with no standard meaning.",
                "kb-t05", "industry-standard encryption", 0.90,
            ),
            _finding(
                DRAFT_ID, "u3", "fact", "major", "3 months free on the Business tier",
                "This promotion has ended. The supporting fact is expired, so a person must confirm or remove the offer.",
                "kb-f04", None, 0.85,
            ),
            _finding(
                DRAFT_ID, "u3", "terminology", "minor", "Log in",
                "Use 'sign in' instead of 'log in'.",
                "kb-t02", "Sign in", 0.93,
            ),
            _finding(
                DRAFT_ID, "u3", "tone", "minor", "see why teams love us!",
                "Hype phrasing and an exclamation mark conflict with the brand voice.",
                "kb-g01", "see what your team can build.", 0.80,
            ),
        ]
    )
    for offset, action, details in [
        (12, "submission_created", "Title 'NovaTech Cloud 3.0 launch email' (text), 3 content units"),
        (11, "analysis_completed", "7 findings (2 critical)"),
    ]:
        add_audit(
            db,
            DRAFT_ID,
            "creator",
            "Creator",
            action,
            details,
            timestamp=utcnow() - timedelta(minutes=offset),
        )


def _seed_approved_draft(db: Session) -> None:
    if db.get(Submission, APPROVED_ID) is not None:
        return
    created = utcnow() - timedelta(days=3)
    approved = utcnow() - timedelta(days=2, hours=20)
    db.add(
        Submission(
            id=APPROVED_ID,
            workspace_id=WORKSPACE_ID,
            title="Security overview one-pager",
            source_format="text",
            original_content=APPROVED_ORIGINAL,
            current_content=APPROVED_CURRENT,
            version=2,
            status="approved",
            created_by="Creator",
            created_at=created,
            approved_by="Approver",
            approved_at=approved,
        )
    )
    db.flush()
    db.add(
        _finding(
            APPROVED_ID, "u1", "terminology", "major", "Our users",
            "Use 'customers' instead of 'users'.",
            "kb-t01", "Our customers", 0.95,
            status="accepted",
        )
    )
    for delta, role, name, action, details in [
        (
            timedelta(0), "creator", "Creator", "submission_created",
            "Title 'Security overview one-pager' (text), 1 content unit",
        ),
        (timedelta(minutes=1), "creator", "Creator", "analysis_completed", "1 finding (0 critical)"),
        (
            timedelta(hours=2), "creator", "Creator", "finding_accepted",
            "'Our users' -> 'Our customers' (now v2)",
        ),
        (
            approved - created, "approver", "Approver", "approved",
            "Facts match SOC 2 report and MSA. Approved.",
        ),
    ]:
        add_audit(db, APPROVED_ID, role, name, action, details, timestamp=created + delta)


def _seed(db: Session) -> None:
    if db.get(Workspace, WORKSPACE_ID) is None:
        db.add(Workspace(id=WORKSPACE_ID, name=WORKSPACE_NAME, brand_palette_hex=PALETTE))
        db.flush()
    for row in KNOWLEDGE:
        if db.get(KnowledgeEntry, row["id"]) is None:
            db.add(KnowledgeEntry(workspace_id=WORKSPACE_ID, version=1, **row))
    db.flush()
    _seed_review_draft(db)
    _seed_approved_draft(db)


def seed_demo_workspace(db: Optional[Session] = None) -> None:
    """Idempotent startup seeder called by main.py lifespan."""
    own = db is None
    db = db or SessionLocal()
    try:
        _seed(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()