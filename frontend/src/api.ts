export type Role = 'Creator' | 'Approver' | 'Admin';
export type Severity = 'critical' | 'major' | 'minor';
export type Status = 'Draft' | 'In Review' | 'Revision Required' | 'Approved' | 'Rejected';
export interface Evidence { title: string; quote: string; owner: string; expires_at?: string | null; expired?: boolean }
export interface Finding {
  id: string; category: 'Brand' | 'Fact' | 'Accessibility' | 'Legal' | string; severity: Severity; confidence: number;
  explanation: string; original: string; suggested: string;
  status: 'open' | 'accepted' | 'rejected' | 'disputed'; dispute_reason?: string; evidence: Evidence;
}
export interface AuditEvent { at: string; actor: string; role?: string; action: string; detail?: string }
export interface Submission {
  id: string; title: string; content: string; version: number; status: Status;
  findings?: Finding[]; audit?: AuditEvent[]; updated_at?: string;
}
export interface KbItem { id: string; kind: 'guideline' | 'fact' | 'term'; title: string; body: string; owner?: string; expires_at?: string | null; expired?: boolean; preferred?: string; banned?: string }
export interface Analytics {
  total_submissions: number; approval_rate: number;
  top_rules: { rule: string; count: number }[]; by_category: { category: string; count: number }[];
  disputed_rules: { rule: string; disputes: number }[];
}

import { mockReq } from './mock';
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

let role: Role = 'Creator';export const setApiRole = (r: Role) => { role = r; };

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (USE_MOCK) return mockReq(path, init, role);
  const isForm = init.body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), 'X-Demo-Role': role.toLowerCase(), ...(init.headers || {}) },
  });
  if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Request failed (${res.status})`);
  return res.status === 204 ? (undefined as T) : res.json();
}
const body = (o: unknown) => JSON.stringify(o);

const statusNames: Record<string, Status> = {
  draft: 'Draft',
  in_review: 'In Review',
  revision_required: 'Revision Required',
  approved: 'Approved',
  rejected: 'Rejected',
};

interface ApiFinding {
  finding_id?: string;
  id?: string;
  original_span?: string;
  original?: string;
  suggested_edit?: string | null;
  suggested?: string;
  category: Finding['category'];
  severity: Severity;
  confidence: number;
  explanation: string;
  status: Finding['status'];
  dispute_reason?: string;
  evidence?: {
    title?: string;
    content?: string;
    quote?: string;
    owner?: string | null;
    expiry_date?: string | null;
    expires_at?: string | null;
    is_expired?: boolean;
    expired?: boolean;
  } | null;
}

interface ApiAuditEvent {
  timestamp?: string;
  at?: string;
  actor: string;
  role?: string;
  action: string;
  detail?: string;
}

interface ApiSubmission {
  submission_id?: string;
  id?: string;
  title: string;
  status: string;
  version: number;
  current_text?: string;
  content?: string;
  findings?: ApiFinding[];
  audit_log?: ApiAuditEvent[];
  audit?: ApiAuditEvent[];
  created_at?: string;
  updated_at?: string;
}

interface ApiKnowledge {
  id: string;
  entry_type?: string;
  kind?: KbItem['kind'];
  title?: string;
  preferred?: string;
  banned?: string;
  rule_text?: string;
  body?: string;
  owner?: string | null;
  expiry_date?: string | null;
  expires_at?: string | null;
  is_expired?: boolean;
  expired?: boolean;
}

function mapStatus(status: string): Status {
  return statusNames[status] ?? status as Status;
}

function mapFinding(raw: ApiFinding, currentText: string): Finding {
  const original = raw.original_span ?? raw.original ?? '';
  const proposed = raw.suggested_edit ?? raw.suggested ?? '';
  const suggested = typeof proposed === 'string' && proposed.trim() && original && currentText.includes(original)
    ? proposed
    : '';
  const evidence = raw.evidence ?? {};
  return {
    id: raw.finding_id ?? raw.id ?? '',
    category: raw.category,
    severity: raw.severity,
    confidence: raw.confidence,
    explanation: raw.explanation,
    original,
    suggested,
    status: raw.status,
    dispute_reason: raw.dispute_reason,
    evidence: {
      title: evidence.title ?? '',
      quote: evidence.content ?? evidence.quote ?? '',
      owner: evidence.owner ?? '',
      expires_at: evidence.expiry_date ?? evidence.expires_at ?? null,
      expired: evidence.is_expired ?? evidence.expired ?? false,
    },
  };
}

function mapSubmission(raw: ApiSubmission): Submission {
  const content = raw.current_text ?? raw.content ?? '';
  return {
    id: raw.submission_id ?? raw.id ?? '',
    title: raw.title,
    content,
    version: raw.version,
    status: mapStatus(raw.status),
    findings: (raw.findings ?? []).map(finding => mapFinding(finding, content)),
    audit: (raw.audit_log ?? raw.audit ?? []).map(event => ({
      at: event.timestamp ?? event.at ?? '',
      actor: event.actor,
      role: event.role,
      action: event.action,
      detail: event.detail,
    })),
    updated_at: raw.created_at ?? raw.updated_at,
  };
}

function mapKnowledge(raw: ApiKnowledge): KbItem {
  const entryType = raw.entry_type ?? raw.kind;
  const kind: KbItem['kind'] = entryType === 'preferred_term' || entryType === 'banned_term'
    ? 'term'
    : entryType === 'fact' ? 'fact' : 'guideline';
  return {
    id: raw.id,
    kind,
    title: raw.title ?? raw.preferred ?? raw.banned ?? '',
    body: raw.rule_text ?? raw.body ?? '',
    owner: raw.owner ?? '',
    expires_at: raw.expiry_date ?? raw.expires_at ?? null,
    expired: raw.is_expired ?? raw.expired ?? false,
    preferred: raw.preferred ?? (entryType === 'preferred_term' ? raw.title : undefined),
    banned: raw.banned ?? (entryType === 'banned_term' ? raw.title : undefined),
  };
}

function knowledgePayload(item: Partial<KbItem>, entryType: 'guideline' | 'fact' | 'preferred_term' | 'banned_term') {
  const isTerm = entryType === 'preferred_term' || entryType === 'banned_term';
  const term = entryType === 'preferred_term' ? item.preferred : item.banned;
  return {
    entry_type: entryType,
    title: isTerm ? term : item.title,
    rule_text: item.body ?? '',
    owner: item.owner || null,
    expiry_date: item.expires_at || null,
    severity: 'minor',
  };
}

export const api = {
  listSubmissions: async () => (await req<ApiSubmission[]>('/submissions')).map(mapSubmission),
  getSubmission: async (id: string) => mapSubmission(await req<ApiSubmission>(`/submissions/${id}`)),
  createText: (title: string, content: string) => {
    if (USE_MOCK) return req<Submission>('/submissions', { method: 'POST', body: body({ title, content }) });
    const form = new FormData(); form.append('title', title); form.append('text', content);
    return req<ApiSubmission>('/submissions', { method: 'POST', body: form }).then(mapSubmission);
  },
  createFile: (file: File, title: string) => {
    const form = new FormData(); form.append('file', file); form.append('title', title);
    return req<ApiSubmission>('/submissions', { method: 'POST', body: form }).then(mapSubmission);
  },
  patchFinding: (id: string, action: 'accept' | 'reject' | 'dispute', reason?: string) =>
    req<ApiSubmission>(`/findings/${id}`, { method: 'PATCH', body: body(USE_MOCK ? { action, reason } : { action, dispute_reason: reason }) }).then(mapSubmission),
  decide: (id: string, decision: 'approve' | 'revise' | 'reject', comment: string) => {
    const mappedDecision = decision === 'revise' ? 'request_revision' : decision;
    const path = USE_MOCK ? `/submissions/${id}/decision` : `/submissions/${id}/decide`;
    return req<ApiSubmission>(path, { method: 'POST', body: body({ decision: USE_MOCK ? decision : mappedDecision, comment }) }).then(mapSubmission);
  },
  listKb: async () => (await req<ApiKnowledge[]>(USE_MOCK ? '/kb' : '/knowledge')).map(mapKnowledge),
  addKb: async (item: Partial<KbItem>) => {
    if (USE_MOCK) return mapKnowledge(await req<ApiKnowledge>('/kb', { method: 'POST', body: body(item) }));
    const entryTypes: Array<'guideline' | 'fact' | 'preferred_term' | 'banned_term'> = [];
    if (item.kind === 'term') {
      if (item.preferred) entryTypes.push('preferred_term');
      if (item.banned) entryTypes.push('banned_term');
    } else if (item.kind) {
      entryTypes.push(item.kind);
    }
    if (!entryTypes.length) throw new Error('Choose a preferred or banned term before adding this entry.');
    const created = await Promise.all(entryTypes.map(entryType =>
      req<ApiKnowledge>('/knowledge', { method: 'POST', body: body(knowledgePayload(item, entryType)) }),
    ));
    return mapKnowledge(created[0]);
  },
  analytics: () => req<Analytics>('/analytics'),
};

export const SAMPLE_DRAFT = `Introducing NovaTech Cloud Pro — the world's #1 cloud platform, with 100% uptime guaranteed and zero security risk.
Our Q4 Enterprise Cloud launch cuts customer costs by 80% compared to any competitor. Just ping the guys in sales to get free, unlimited storage forever.
[Image: team photo]  Click here to learn more.`;
