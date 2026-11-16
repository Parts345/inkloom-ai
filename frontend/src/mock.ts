import type { Analytics, AuditEvent, Finding, KbItem, Severity, Status, Submission } from './api';

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
    if (init.body instanceof FormData) {
      const f = init.body.get('file') as File; title = init.body.get('title') || f.name;
      content = /\.txt$/i.test(f.name) ? await f.text() : SAMPLE;
    }
    const s = mk(id, title || 'Untitled draft', content || ''); s.audit = [audit(actor, 'Submitted draft'), audit('Inkloom AI', 'Automated review complete')];
    subs.unshift(s); return structuredClone(s);
  }
  if ((m = path.match(/^\/findings\/([^/]+)$/))) {
    const s = subs.find(x => x.findings?.some(f => f.id === m![1])); const f = s?.findings?.find(x => x.id === m![1]);
    if (!s || !f) throw new Error('Finding not found');
    if (json.action === 'accept') { s.content = s.content.replace(f.original, f.suggested); s.version += 1; f.status = 'accepted'; }
    else if (json.action === 'reject') f.status = 'rejected';
    else { f.status = 'disputed'; f.dispute_reason = json.reason; }
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
      disputed_rules: count(f => f.evidence.title, f => f.status === 'disputed').map(([rule, disputes]) => ({ rule, disputes })) };
    if (!a.disputed_rules.length) a.disputed_rules = [{ rule: 'Offer Terms Policy', disputes: 2 }, { rule: 'Brand Voice Guide §4.2', disputes: 1 }];
    return a;
  }
  throw new Error(`Mock: no route for ${method} ${path}`);
}