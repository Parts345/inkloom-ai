export type Role = 'Creator' | 'Approver' | 'Admin';
export type Severity = 'critical' | 'major' | 'minor';
export type Status = 'In Review' | 'Revision Required' | 'Approved' | 'Rejected';
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
const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

let role: Role = 'Creator';export const setApiRole = (r: Role) => { role = r; };

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (USE_MOCK) return mockReq(path, init, role);
  const isForm = init.body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), 'X-Demo-Role': role, ...(init.headers || {}) },
  });
  if (!res.ok) throw new Error((await res.text().catch(() => '')) || `Request failed (${res.status})`);
  return res.status === 204 ? (undefined as T) : res.json();
}
const body = (o: unknown) => JSON.stringify(o);

export const api = {
  listSubmissions: () => req<Submission[]>('/submissions'),
  getSubmission: (id: string) => req<Submission>(`/submissions/${id}`),
  createText: (title: string, content: string) => req<Submission>('/submissions', { method: 'POST', body: body({ title, content }) }),
  createFile: (file: File, title: string) => { const f = new FormData(); f.append('file', file); f.append('title', title); return req<Submission>('/submissions', { method: 'POST', body: f }); },
  patchFinding: (id: string, action: 'accept' | 'reject' | 'dispute', reason?: string) =>
    req<Submission | Finding>(`/findings/${id}`, { method: 'PATCH', body: body({ action, reason }) }),
  decide: (id: string, decision: 'approve' | 'revise' | 'reject', comment: string) =>
    req<Submission>(`/submissions/${id}/decision`, { method: 'POST', body: body({ decision, comment }) }),
  listKb: () => req<KbItem[]>('/kb'),
  addKb: (item: Partial<KbItem>) => req<KbItem>('/kb', { method: 'POST', body: body(item) }),
  analytics: () => req<Analytics>('/analytics'),
};

export const SAMPLE_DRAFT = `Introducing NovaTech Cloud Pro — the world's #1 cloud platform, with 100% uptime guaranteed and zero security risk.
Our Q4 Enterprise Cloud launch cuts customer costs by 80% compared to any competitor. Just ping the guys in sales to get free, unlimited storage forever.
[Image: team photo]  Click here to learn more.`;
