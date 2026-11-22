import type { Analytics, ApprovedAsset, AssetType, AuditEvent, DisputeCluster, Finding, KbItem, Severity, Status, Submission } from './api';

const SAMPLE = `Introducing NovaTech Cloud Pro — the world's #1 cloud platform, with 100% uptime guaranteed and zero security risk.
Our Q4 Enterprise Cloud launch cuts customer costs by 80% compared to any competitor. Just ping the guys in sales to get free, unlimited storage forever.
[Image: team photo]  Click here to learn more.`;

const D = (days: number) => new Date(Date.now() + days * 864e5).toISOString();
type T = [string, Severity, number, string, string, string, [string, string, string, number | null]];
const TEMPLATES: T[] = [
  ['Fact', 'critical', 0.97, '100% uptime guaranteed', '99.95% uptime SLA', 'The only approved uptime claim is the contractual SLA. A 100% guarantee creates legal exposure.', ['Cloud SLA v3.2', 'NovaTech Cloud Pro delivers a 99.95% monthly uptime SLA.', 'Platform Reliability', 120]],
  ['Fact', 'critical', 0.93, 'zero security risk', 'enterprise-grade security controls', 'Absolute security claims cannot be verified. Use the approved security wording.', ['Security Claims Policy', 'Never state or imply that any product is free of security risk.', 'Legal & Compliance', 365]],
  ['Fact', 'critical', 0.9, 'cuts customer costs by 80% compared to any competitor', 'can reduce infrastructure costs for eligible workloads', 'The 80% savings figure comes from a benchmark that has expired, and comparative claims need legal sign-off.', ['Cost Benchmark Q2 (expired)', 'Eligible workloads saw up to 80% lower cost versus on-prem baselines.', 'Product Marketing', -45]],
  ['Brand', 'major', 0.88, "the world's #1 cloud platform", 'a trusted enterprise cloud platform', 'Superlative ranking claims are banned unless backed by a cited third-party report.', ['Brand Voice Guide §4.2', 'Avoid unsubstantiated superlatives such as “#1” or “best in the world”.', 'Brand Team', null]],
  ['Brand', 'major', 0.85, 'the guys in sales', 'our sales team', 'Informal, gendered phrasing does not match the NovaTech tone of voice.', ['Brand Voice Guide §2.1', 'Use inclusive, professional language. Say “team”, not “guys”.', 'Brand Team', null]],
  ['Brand', 'minor', 0.8, 'free, unlimited storage forever', 'storage included in your plan tier', 'Offer terms must match the pricing page. “Unlimited” and “forever” are not offered.', ['Offer Terms Policy', 'Do not describe any storage tier as unlimited.', 'Pricing Team', 200]],
  ['Accessibility', 'minor', 0.91, 'Click here', 'Read the NovaTech Cloud Pro guide', 'Link text must describe its destination for screen reader users.', ['WCAG 2.2 · 2.4.4', 'Link purpose can be determined from the link text.', 'Accessibility Lead', null]],
  ['Accessibility', 'minor', 0.86, '[Image: team photo]', '[Image: NovaTech support team at a customer workshop]', 'The image alt text is too vague to convey what the photo shows.', ['WCAG 2.2 · 1.1.1', 'Images need text alternatives that serve the equivalent purpose.', 'Accessibility Lead', null]],
];

const dataUri = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
const LOGO_URI = dataUri('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect width="200" height="200" fill="#F4EDE0"/><circle cx="100" cy="100" r="70" fill="#14213D"/><circle cx="100" cy="100" r="50" fill="#FF6B5B"/><text x="100" y="110" font-family="Arial" font-size="30" fill="#F4EDE0" text-anchor="middle">N</text></svg>');
const BANNER_URI = dataUri('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120"><rect width="400" height="120" fill="#14213D"/><rect x="20" y="20" width="360" height="80" fill="#F4EDE0"/><text x="200" y="75" font-family="Arial" font-size="22" fill="#14213D" text-anchor="middle">NovaTech Banner</text></svg>');
const STYLE_URI = dataUri('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect width="200" height="200" fill="#F4EDE0"/><rect x="20" y="40" width="60" height="60" fill="#14213D"/><rect x="100" y="40" width="60" height="60" fill="#FF6B5B"/><rect x="20" y="120" width="60" height="60" fill="#FF6B5B"/><rect x="100" y="120" width="60" height="60" fill="#14213D"/></svg>');
const FLAGGED_URI = dataUri('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect width="200" height="200" fill="#F4EDE0"/><circle cx="150" cy="60" r="40" fill="#14213D"/><circle cx="150" cy="60" r="24" fill="#FF6B5B"/></svg>');

const ASSETS: ApprovedAsset[] = [
  { id: 'a-logo-1', name: 'NovaTech Primary Logo', asset_type: 'Logo', usage_guidelines: 'Logo must sit within the approved safe zone with at least 20px clear space. Do not stretch or recolour.', image_url: LOGO_URI, uploaded_by: 'Maya (Creator)', uploaded_at: D(-10) },
  { id: 'a-banner-1', name: 'Hero Banner – Q4', asset_type: 'Banner', usage_guidelines: 'Use on campaign landing pages only. Keep the tagline within the safe area.', image_url: BANNER_URI, uploaded_by: 'Maya (Creator)', uploaded_at: D(-5) },
  { id: 'a-style-1', name: 'NovaTech Style Reference', asset_type: 'Style Reference', usage_guidelines: 'Reference palette: navy #14213D, cream #F4EDE0, coral #FF6B5B. Body text at 16pt.', image_url: STYLE_URI, uploaded_by: 'Maya (Creator)', uploaded_at: D(-1) },
];
const assets = structuredClone(ASSETS);
const CLUSTERS: DisputeCluster[] = [
  { rule_id: 'r-offer-terms', rule_title: 'Offer Terms Policy', dispute_count: 7, sample_reasons: ['The “unlimited” claim is still live on the pricing page.', 'Storage tiers contradict the offer terms.', '“Forever” is not a real commitment.'], current_text: 'Do not describe any storage tier as unlimited.', suggested_text: 'Describe storage only by the plan tier allowance; avoid “unlimited” and “forever”.', rationale: 'Legal flagged absolute and comparative claims as unverifiable.', status: 'pending' },
  { rule_id: 'r-superlatives', rule_title: 'Brand Voice Guide §4.2', dispute_count: 4, sample_reasons: ['“#1” appears in hero copy again.', 'Superlatives need a cited source.', 'Ranking claims are not allowed in ads.'], current_text: 'Avoid unsubstantiated superlatives such as “#1” or “best in the world”.', suggested_text: 'Do not use unsubstantiated superlatives such as “#1” or “best in the world” without a cited third-party report.', status: 'pending' },
  { rule_id: 'r-link-text', rule_title: 'WCAG 2.2 · 2.4.4', dispute_count: 2, sample_reasons: ['“Click here” does not say where the link goes.', 'Link text must describe its destination.'], current_text: 'Link purpose can be determined from the link text.', suggested_text: 'Link text must describe its destination so screen reader users know where it leads.', status: 'pending' },
];

const SCANNED_CONTENT = `Scanned Enterprise Flyer (PDF)

Introducing NovaTech Cloud Pro — the world's #1 cloud platform, with 100% uptime guaranteed and zero security risk.
Our Q4 Enterprise Cloud launch cuts customer costs by 80% compared to any competitor.

[Page 2] NovaTech logo placement off-grid`;

const scannedFindings = (sid: string): Finding[] => [
  {
    id: `${sid}-f-visual`, category: 'visual', severity: 'critical', confidence: 0.95,
    original: 'NovaTech logo placement off-grid', suggested: '',
    explanation: 'The logo appears outside the approved safe zone on page 2.',
    status: 'open',
    evidence: { title: 'Brand Voice Guide §4.2', quote: 'Logo must sit within the approved safe zone.', owner: 'Brand Team', expires_at: null, expired: false },
    page_number: 2,
    visual: {
      image_url: FLAGGED_URI,
      asset_id: 'a-logo-1',
      asset_name: 'NovaTech Primary Logo',
      asset_type: 'Logo',
      asset_image_url: LOGO_URI,
      guideline: 'Logo must sit within the approved safe zone with at least 20px clear space.',
      recommended_correction: 'Move the logo back into the safe zone and restore at least 20px of clear space.',
    },
  },
  {
    id: `${sid}-f-ocr-brand`, category: 'Brand', severity: 'major', confidence: 0.9,
    extraction_method: 'ocr', page_number: 1,
    original: "the world's #1 cloud platform", suggested: 'a trusted enterprise cloud platform',
    explanation: 'OCR detected a banned superlative on page 1.',
    status: 'open',
    evidence: { title: 'Brand Voice Guide §4.2', quote: 'Avoid unsubstantiated superlatives such as “#1”.', owner: 'Brand Team', expires_at: null, expired: false },
  },
  {
    id: `${sid}-f-ocr-fact`, category: 'Fact', severity: 'major', confidence: 0.93,
    extraction_method: 'ocr', page_number: 1,
    original: '100% uptime guaranteed', suggested: '99.95% uptime SLA',
    explanation: 'OCR detected an unverified uptime guarantee on page 1.',
    status: 'open',
    evidence: { title: 'Cloud SLA v3.2', quote: 'The only approved uptime claim is the contractual SLA.', owner: 'Platform Reliability', expires_at: null, expired: false },
  },
];

const now = () => new Date().toISOString();
const mkFindings = (sid: string, content: string): Finding[] =>
  TEMPLATES.filter(t => content.includes(t[3])).map((t, i) => ({
    id: `${sid}-f${i + 1}`, category: t[0], severity: t[1], confidence: t[2], original: t[3], suggested: t[4], explanation: t[5], status: 'open',
    evidence: { title: t[6][0], quote: t[6][1], owner: t[6][2], expires_at: t[6][3] === null ? null : D(t[6][3]), expired: t[6][3] !== null && t[6][3] < 0 } }));

const audit = (actor: string, action: string, detail?: string): AuditEvent => ({ at: now(), actor, action, detail });
const mk = (id: string, title: string, content: string, status: Status = 'In Review'): Submission =>
  ({ id, title, content, version: 1, status, findings: mkFindings(id, content), audit: [audit('Maya (Creator)', 'Submitted draft'), audit('Inkloom AI', 'Automated review complete', 'Checked brand, facts and accessibility')] });

const subs: Submission[] = [
  mk('s1', 'Q4 Enterprise Cloud Launch Post', SAMPLE),
  { ...mk('s4', 'Scanned Enterprise Flyer (PDF)', SCANNED_CONTENT, 'In Review'), findings: scannedFindings('s4') },
  { ...mk('s2', 'Holiday Promo Email', 'Save on NovaTech Cloud plans this season. Read the pricing guide for details.', 'Approved'), updated_at: now() },
  { ...mk('s3', 'Partner Webinar Invite', 'Join us. Click here to register for the NovaTech partner webinar.', 'Revision Required') },
];
const kb: KbItem[] = [
  { id: 'k1', kind: 'guideline', title: 'Brand Voice Guide §4.2', body: 'Avoid unsubstantiated superlatives such as “#1”.', owner: 'Brand Team' },
  { id: 'k2', kind: 'guideline', title: 'WCAG 2.2 · 2.4.4', body: 'Link text must describe the destination.', owner: 'Accessibility Lead' },
  { id: 'k3', kind: 'fact', title: 'Cloud SLA v3.2', body: '99.95% monthly uptime SLA.', owner: 'Platform Reliability', expires_at: D(120), expired: false },
  { id: 'k4', kind: 'fact', title: 'Cost Benchmark Q2', body: 'Up to 80% lower cost versus on-prem baselines.', owner: 'Product Marketing', expires_at: D(-45), expired: true },
  { id: 'k5', kind: 'term', title: 'guys', body: 'Use inclusive language', preferred: 'team', banned: 'guys' },
  { id: 'k6', kind: 'term', title: 'ping', body: 'Too casual for external copy', preferred: 'contact', banned: 'ping' },
];
let n = 10;
const delay = () => new Promise(r => setTimeout(r, 150));

export async function mockReq(path: string, init: RequestInit, role: string): Promise<any> {
  await delay();
  const method = (init.method ?? 'GET').toUpperCase(); const actor = `Demo ${role}`;
  const json = typeof init.body === 'string' ? JSON.parse(init.body) : {};
  let m: RegExpMatchArray | null;
  if (path === '/submissions' && method === 'GET') return subs.map(({ findings, audit, ...s }) => s);
  if ((m = path.match(/^\/submissions\/([^/]+)$/))) { const s = subs.find(x => x.id === m![1]); if (!s) throw new Error('Not found'); return structuredClone(s); }
  if (path === '/submissions' && method === 'POST') {
    const id = `s${++n}`; let title = json.title, content = json.content;
    let findings: Finding[] | undefined;
    if (init.body instanceof FormData) {
      const f = init.body.get('file') as File; title = init.body.get('title') || f.name;
      if (/\.txt$/i.test(f.name)) { content = await f.text(); }
      else if (/\.(png|jpe?g|pdf)$/i.test(f.name)) { content = SCANNED_CONTENT; findings = scannedFindings(id); }
      else { content = SAMPLE; }
    }
    const s = mk(id, title || 'Untitled draft', content || '');
    if (findings) s.findings = findings;
    s.audit = [audit(actor, 'Submitted draft'), audit('Inkloom AI', 'Automated review complete')];
    subs.unshift(s); return structuredClone(s);
  }
  if ((m = path.match(/^\/findings\/([^/]+)$/))) {
    const s = subs.find(x => x.findings?.some(f => f.id === m![1])); const f = s?.findings?.find(x => x.id === m![1]);
    if (!s || !f) throw new Error('Finding not found');
    if (json.action === 'accept') { s.content = s.content.replace(f.original, f.suggested); s.version += 1; f.status = 'accepted'; }
    else if (json.action === 'reject') f.status = 'rejected';
    else { f.status = 'disputed'; f.dispute_reason = json.reason; const cl = CLUSTERS.find(c => c.rule_title === f.evidence.title); if (cl) { cl.dispute_count += 1; if (json.reason) cl.sample_reasons.unshift(json.reason); } }
    s.audit!.push(audit(actor, `${json.action === 'accept' ? 'Accepted' : json.action === 'reject' ? 'Rejected' : 'Disputed'} edit`, `${f.category}: ${f.original}`));
    return structuredClone(s);
  }
  if ((m = path.match(/^\/submissions\/([^/]+)\/decision$/))) {
    const s = subs.find(x => x.id === m![1]); if (!s) throw new Error('Not found');
    if (role === 'Creator') throw new Error('Only Approvers and Admins can decide.');
    s.status = ({ approve: 'Approved', revise: 'Revision Required', reject: 'Rejected' } as const)[json.decision as 'approve'];
    s.audit!.push(audit(actor, `Decision: ${s.status}`, json.comment)); return structuredClone(s);
  }
  if (path === '/kb' && method === 'GET') return structuredClone(kb);
  if (path === '/kb' && method === 'POST') {
    if (role !== 'Admin') throw new Error('Only Admins can edit the knowledge base.');
    const k: KbItem = { ...json, id: `k${++n}`, expired: json.expires_at ? new Date(json.expires_at) < new Date() : false, title: json.title || json.preferred || 'Untitled' };
    kb.push(k); return k;
  }
  if (path === '/analytics') {
    const all = subs.flatMap(s => s.findings ?? []); const count = (key: (f: Finding) => string, pick: (f: Finding) => boolean = () => true) => {
      const c: Record<string, number> = {}; all.filter(pick).forEach(f => { c[key(f)] = (c[key(f)] ?? 0) + 1; }); return Object.entries(c).sort((a, b) => b[1] - a[1]); };
    const decided = subs.filter(s => s.status === 'Approved' || s.status === 'Rejected');
    const a: Analytics = {
      total_submissions: subs.length, approval_rate: decided.length ? subs.filter(s => s.status === 'Approved').length / decided.length : 0,
      top_rules: count(f => f.evidence.title).slice(0, 5).map(([rule, count]) => ({ rule, count })),
      by_category: count(f => f.category).map(([category, count]) => ({ category, count })),
      disputed_rules: CLUSTERS.filter(c => c.status !== 'published').map(c => ({ rule: c.rule_title, disputes: c.dispute_count })) };
    return a;
  }
  if (path === '/knowledge/assets' && method === 'GET') return structuredClone(assets);
  if (path === '/knowledge/assets/upload' && method === 'POST') {
    if (role === 'Creator') throw new Error('Only Approvers and Admins can upload approved assets.');
    if (!(init.body instanceof FormData)) throw new Error('Expected form data.');
    const file = init.body.get('file') as File;
    const asset: ApprovedAsset = {
      id: `a-${++n}`,
      name: (init.body.get('name') as string) || file.name,
      asset_type: (init.body.get('asset_type') as AssetType) ?? 'Logo',
      usage_guidelines: (init.body.get('usage_guidelines') as string) ?? '',
      image_url: URL.createObjectURL(file),
      uploaded_by: `Demo ${role}`,
      uploaded_at: now(),
    };
    assets.unshift(asset);
    return structuredClone(asset);
  }
  if (path === '/analytics/disputed-rules' && method === 'GET') return CLUSTERS.filter(c => c.status !== 'published');
  if ((m = path.match(/^\/knowledge\/rules\/([^/]+)\/apply-refinement$/))) {
    if (role !== 'Admin') throw new Error('Only Admins can publish rule refinements.');
    const id = decodeURIComponent(m[1]); const cl = CLUSTERS.find(c => c.rule_id === id);
    if (!cl) throw new Error('Rule not found.');
    cl.status = 'published';
    cl.current_text = (json.suggested_text as string) ?? cl.current_text;
    return { rule_id: cl.rule_id, rule_title: cl.rule_title, published_at: now() };
  }
  throw new Error(`Mock: no route for ${method} ${path}`);
}