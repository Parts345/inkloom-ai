import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Check, X, Flag, History, ShieldAlert, Plus } from 'lucide-react';
import { api, Finding, Role, Submission } from '../api';
import { Card, ErrorNote, SeverityBadge, Spinner, StatusPill, fmt, markStyle } from './ui';

interface Props { role: Role; submissions: Submission[]; activeId: string | null; onSelect: (id: string) => void; onChanged: () => void; onNew: () => void }

function Highlighted({ text, findings, focus }: { text: string; findings: Finding[]; focus: string | null }) {
  const parts = useMemo(() => {
    const spans = findings.filter(f => f.status === 'open' || f.status === 'disputed')
      .map(f => ({ f, i: text.indexOf(f.original) })).filter(x => x.i >= 0 && x.f.original).sort((a, b) => a.i - b.i);
    const out: React.ReactNode[] = []; let pos = 0;
    for (const { f, i } of spans) {
      if (i < pos) continue;
      out.push(text.slice(pos, i));
      out.push(<mark key={f.id} id={`span-${f.id}`} title={`${f.category}: ${f.severity}`}
        className={`rounded-sm px-0.5 text-inherit ${markStyle[f.severity]} ${focus === f.id ? 'ring-2 ring-slate-900' : ''}`}>{f.original}</mark>);
      pos = i + f.original.length;
    }
    out.push(text.slice(pos)); return out;
  }, [text, findings, focus]);
  return <p className="whitespace-pre-wrap font-serif text-[15px] leading-7">{parts}</p>;
}

function FindingCard({ f, onAct, focused, onFocus }: { f: Finding; onAct: (a: 'accept' | 'reject' | 'dispute', r?: string) => Promise<void>; focused: boolean; onFocus: () => void }) {
  const [ev, setEv] = useState(false); const [dispute, setDispute] = useState(false); const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false);
  const done = f.status !== 'open';
  const act = async (a: 'accept' | 'reject' | 'dispute', r?: string) => { setBusy(true); try { await onAct(a, r); setDispute(false); } finally { setBusy(false); } };
  return (
    <Card className={`p-4 ${focused ? 'ring-2 ring-slate-900' : ''} ${done ? 'opacity-70' : ''}`}>
      <div className="flex flex-wrap items-center gap-2" onClick={onFocus}>
        <SeverityBadge s={f.severity} /><span className="text-sm font-semibold capitalize">{f.category.charAt(0).toUpperCase() + f.category.slice(1)}</span>
        {f.extraction_method === 'ocr' && <span className="rounded border border-slate-300 bg-slate-100 px-2 py-0.5 text-xs text-slate-600" aria-label="OCR extracted">OCR Extracted{f.page_number ? ` (Page ${f.page_number})` : ''}</span>}
        <span className="ml-auto text-xs text-slate-500">Confidence {Math.round(f.confidence * (f.confidence <= 1 ? 100 : 1))}%</span>
        {done && <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium capitalize">{f.status}</span>}
      </div>
      <p className="mt-2 text-sm text-slate-700">{f.explanation}</p>
      {f.visual && (
        <div className="mt-3 rounded-md border border-slate-200 bg-white p-3">
          <h4 className="mb-2 text-sm font-semibold text-slate-900">Visual check{f.page_number ? ` · Page ${f.page_number}` : ''}</h4>
          <div className="grid gap-3 md:grid-cols-2">
            <figure className="p-2 border border-red-200 rounded bg-red-50">
              <img src={f.visual.image_url} alt={`Flagged image: ${f.original}`} className="w-full h-24 rounded object-cover border border-red-500" />
              <figcaption className="mt-1 text-xs text-red-800 font-medium text-center">Flagged image</figcaption>
            </figure>
            <figure className="p-2 border border-emerald-200 rounded bg-emerald-50">
              <img src={f.visual.asset_image_url} alt={`Approved asset: ${f.visual.asset_name}`} className="w-full h-24 rounded object-cover border border-emerald-500" />
              <figcaption className="mt-1 text-xs text-emerald-800 font-medium text-center">Approved {f.visual.asset_type}: {f.visual.asset_name}</figcaption>
            </figure>
          </div>
          <p className="mt-2 text-sm text-slate-700"><span className="font-semibold">Asset guideline: </span>{f.visual.guideline}</p>
          <p className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 p-2 text-sm text-emerald-800"><span className="font-semibold">Recommended visual correction: </span>{f.visual.recommended_correction}</p>
        </div>
      )}
      <button onClick={() => setEv(!ev)} aria-expanded={ev} className="mt-3 flex items-center gap-1 text-sm font-medium text-teal-800">
        {ev ? <ChevronDown size={14} /> : <ChevronRight size={14} />}Source evidence</button>
      {ev && (
        <div className="mt-2 rounded-md border-l-4 border-teal-600 bg-slate-50 p-3 text-sm">
          <p className="font-semibold">{f.evidence.title}</p>
          <blockquote className="my-1 italic text-slate-700">“{f.evidence.quote}”</blockquote>
          <p className="text-xs text-slate-500">Owner: {f.evidence.owner} · {f.evidence.expired
            ? <span className="font-semibold text-red-700">Expired {fmt(f.evidence.expires_at)}</span> : <>Valid until {fmt(f.evidence.expires_at)}</>}</p>
        </div>)}
      <div className="mt-3 rounded-md border border-slate-200 p-3 font-mono text-xs">
        <p className="mb-1 font-sans text-xs font-semibold text-slate-500">Suggested minimal edit</p>
        <p className="bg-red-50 px-1 text-red-800 line-through">{f.original}</p>
        <p className="mt-1 bg-emerald-50 px-1 text-emerald-800">{f.suggested}</p>
        {!f.suggested && <p className="mt-1 italic text-slate-500">Remove this text</p>}
      </div>
      {f.dispute_reason && <p className="mt-2 text-xs text-slate-600">Dispute reason: {f.dispute_reason}</p>}
      {!done && (<>
        <div className="mt-3 flex flex-wrap gap-2">
          <button disabled={busy || !f.suggested.trim()} title={!f.suggested.trim() ? 'No applicable inline text replacement' : undefined} onClick={() => act('accept')} className="flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"><Check size={14} />Accept Edit</button>
          <button disabled={busy} onClick={() => act('reject')} className="flex items-center gap-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium"><X size={14} />Reject</button>
          <button disabled={busy} onClick={() => setDispute(!dispute)} className="flex items-center gap-1 rounded-md border border-amber-400 px-3 py-1.5 text-sm font-medium text-amber-900"><Flag size={14} />Dispute</button>
        </div>
        {dispute && (<div className="mt-2 flex gap-2">
          <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Why is this finding wrong?" className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm" />
          <button disabled={!reason.trim() || busy} onClick={() => act('dispute', reason)} className="rounded-md bg-amber-500 px-3 py-1.5 text-sm font-medium disabled:opacity-40">Send</button></div>)}
      </>)}
    </Card>
  );
}

export default function SubmissionReviewView({ role, submissions, activeId, onSelect, onChanged, onNew }: Props) {
  const [sub, setSub] = useState<Submission | null>(null); const [err, setErr] = useState('');
  const [audit, setAudit] = useState(false); const [comment, setComment] = useState(''); const [focus, setFocus] = useState<string | null>(null);

  const load = async (id: string) => { try { setSub(await api.getSubmission(id)); setErr(''); } catch (e) { setErr((e as Error).message); } };
  useEffect(() => { if (activeId) load(activeId); else setSub(null); }, [activeId]);

  const act = (f: Finding) => async (a: 'accept' | 'reject' | 'dispute', reason?: string) => {
    if (!sub) return;
    try {
      await api.patchFinding(f.id, a, reason);
      if (a === 'accept') setSub(s => s && { ...s, content: s.content.replace(f.original, f.suggested) }); // live left-pane update
      await load(sub.id); onChanged();
    } catch (e) { setErr((e as Error).message); }
  };
  const decide = async (d: 'approve' | 'revise' | 'reject') => {
    if (!sub) return;
    try { setSub(await api.decide(sub.id, d, comment)); setComment(''); onChanged(); } catch (e) { setErr((e as Error).message); }
  };

  const findings = sub?.findings ?? [];
  const openCritical = findings.filter(f => f.severity === 'critical' && (f.status === 'open' || f.status === 'disputed')).length;
  const canReview = role === 'Approver' || role === 'Admin';

  return (
    <div className="pb-28">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select value={activeId ?? ''} onChange={e => onSelect(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm" aria-label="Choose submission">
          {submissions.map(s => <option key={s.id} value={s.id}>{s.title} (v{s.version})</option>)}
        </select>
        <button onClick={onNew} className="ml-auto flex items-center gap-1 rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white"><Plus size={16} />New Submission</button>
      </div>
      {err && <ErrorNote msg={err} />}
      {!submissions.length && <Card className="p-10 text-center text-sm text-slate-600">No submissions yet. Create one to see flagged issues.</Card>}
      {activeId && !sub && !err && <Spinner label="Loading submission…" />}
      {sub && (
        <div className="grid gap-6 lg:grid-cols-2">
          <section aria-label="Draft content">
            <Card className="p-5">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 className="mr-auto text-lg font-semibold">{sub.title}</h2>
                <span className="rounded bg-slate-900 px-2 py-0.5 text-xs font-semibold text-white">v{sub.version}</span><StatusPill s={sub.status} />
              </div>
              <div className="mb-3 flex gap-3 text-xs text-slate-600">
                {(['critical', 'major', 'minor'] as const).map(s => <span key={s} className="flex items-center gap-1"><i className={`h-2.5 w-2.5 rounded-sm ${markStyle[s].split(' ')[0]} border ${s === 'critical' ? 'border-red-500' : s === 'major' ? 'border-amber-500' : 'border-blue-500'}`} />{s}</span>)}
              </div>
              <Highlighted text={sub.content} findings={findings} focus={focus} />
              <button onClick={() => setAudit(!audit)} aria-expanded={audit} className="mt-5 flex items-center gap-2 border-t border-slate-200 pt-3 text-sm font-medium">
                <History size={14} />Audit trail ({sub.audit?.length ?? 0})</button>
              {audit && <ol className="mt-2 space-y-2 border-l-2 border-slate-200 pl-4 text-sm">
                {(sub.audit ?? []).map((a, i) => <li key={i}><b>{a.action}</b> <span className="text-slate-500">by {a.actor}{a.role ? ` (${a.role})` : ''} · {new Date(a.at).toLocaleString()}</span>{a.detail && <p className="text-slate-600">{a.detail}</p>}</li>)}
              </ol>}
            </Card>
          </section>
          <section aria-label="Findings" className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-600">{findings.filter(f => f.status === 'open').length} open of {findings.length} findings</h3>
            {findings.map(f => <FindingCard key={f.id} f={f} onAct={act(f)} focused={focus === f.id}
              onFocus={() => { setFocus(f.id); document.getElementById(`span-${f.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }} />)}
            {!findings.length && <Card className="p-6 text-center text-sm text-slate-600">No issues found.</Card>}
          </section>
        </div>)}
      {sub && canReview && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2 px-4 py-3">
            <input value={comment} onChange={e => setComment(e.target.value)} placeholder="Reviewer comment" aria-label="Reviewer comment" className="min-w-[200px] flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm" />
            <span title={openCritical ? `Resolve ${openCritical} open critical issue(s) first` : ''}>
              <button disabled={openCritical > 0} onClick={() => decide('approve')} className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">Approve Content</button></span>
            <button onClick={() => decide('revise')} className="rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold">Request Revision</button>
            <button onClick={() => decide('reject')} className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white">Reject</button>
            {openCritical > 0 && <span className="flex items-center gap-1 text-xs text-red-700"><ShieldAlert size={14} />{openCritical} critical open</span>}
          </div>
        </div>)}
    </div>
  );
}
