import { useCallback, useEffect, useState } from 'react';
import { Check, X } from 'lucide-react';
import { api, Analytics, DisputeCluster, Role } from '../api';
import { Card, ErrorNote, Spinner } from './ui';

function Bars({ rows, color }: { rows: { label: string; n: number }[]; color: string }) {
  const max = Math.max(1, ...rows.map(r => r.n));
  return <ul className="space-y-2">{rows.map(r => <li key={r.label} className="text-sm">
    <div className="flex justify-between"><span className="truncate pr-2">{r.label}</span><b>{r.n}</b></div>
    <div className="h-2 rounded bg-slate-100"><div className={`h-2 rounded ${color}`} style={{ width: `${(r.n / max) * 100}%` }} /></div></li>)}
    {!rows.length && <li className="text-sm text-slate-500">No data yet.</li>}</ul>;
}

export default function GovernanceAnalyticsView({ role }: { role: Role }) {
  const [a, setA] = useState<Analytics | null>(null);
  const [clusters, setClusters] = useState<DisputeCluster[] | null>(null);
  const [err, setErr] = useState('');
  const [publishing, setPublishing] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ kind: 'success' | 'error'; msg: string } | null>(null);

  const load = useCallback(async () => {
    const [analytics, disputed] = await Promise.all([api.analytics(), api.disputedRules()]);
    setA(analytics);
    setClusters(disputed);
  }, []);

  useEffect(() => { load().catch(e => setErr(e.message)); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4500);
    return () => clearTimeout(t);
  }, [toast]);

  const publish = async (cluster: DisputeCluster) => {
    setPublishing(prev => new Set(prev).add(cluster.rule_id));
    setToast(null);
    try {
      await api.applyRefinement(cluster.rule_id, cluster.suggested_text);
      await load();
      setToast({ kind: 'success', msg: `Published rule refinement for “${cluster.rule_title}”.` });
    } catch (e) {
      setToast({ kind: 'error', msg: (e as Error).message });
    } finally {
      setPublishing(prev => { const next = new Set(prev); next.delete(cluster.rule_id); return next; });
    }
  };

  if (err) return <ErrorNote msg={err} />;
  if (!a || clusters === null) return <Spinner />;

  const rate = a.approval_rate <= 1 ? a.approval_rate * 100 : a.approval_rate;
  const pending = clusters;

  return (
    <div className="space-y-4">
      {toast && (
        <div className="fixed bottom-4 right-4 z-50" role="status" aria-live="polite">
          <div className={`rounded-md border p-3 text-sm flex items-center gap-2 shadow-sm ${toast.kind === 'success' ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-red-50 text-red-800 border-red-200'}`}>
            {toast.kind === 'success' ? <Check size={16} /> : <X size={16} />}
            {toast.msg}
          </div>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="p-5"><p className="text-sm text-slate-500">Total submissions</p><p className="text-3xl font-semibold">{a.total_submissions}</p></Card>
        <Card className="p-5"><p className="text-sm text-slate-500">Approval rate</p><p className="text-3xl font-semibold">{rate.toFixed(0)}%</p>
          <div className="mt-2 h-2 rounded bg-slate-100"><div className="h-2 rounded bg-emerald-500" style={{ width: `${rate}%` }} /></div></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5"><h3 className="mb-3 font-semibold">Top violated rules</h3><Bars color="bg-red-500" rows={a.top_rules.map(r => ({ label: r.rule, n: r.count }))} /></Card>
        <Card className="p-5"><h3 className="mb-3 font-semibold">Issues by category</h3><Bars color="bg-teal-600" rows={a.by_category.map(r => ({ label: r.category, n: r.count }))} /></Card>
      </div>
      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold">Dispute-Driven Rule Refinement</h3>
        {pending.length === 0 ? <p className="text-sm text-slate-500">No disputed rules need clarification right now.</p> : (
          <div className="space-y-4">
            {pending.map(cluster => (
              <div key={cluster.rule_id} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-sm font-semibold">{cluster.rule_title}</h4>
                  <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">{cluster.dispute_count} disputes</span>
                </div>
                <p className="text-xs text-slate-500 mb-1">Sample creator dispute reasons:</p>
                <ul className="space-y-1 text-sm text-slate-700">
                  {cluster.sample_reasons.slice(0, 3).map((r, i) => <li key={i} className="pl-2 italic border-l-2 border-slate-200">“{r}”</li>)}
                </ul>
                <div className="rounded-md border border-slate-200 p-3">
                  <p className="mb-1 text-xs font-semibold text-slate-500">Suggested Rule Update</p>
                  <p className="bg-red-50 px-1 text-red-800 line-through text-sm">{cluster.current_text}</p>
                  <p className="mt-1 bg-emerald-50 px-1 text-emerald-800 text-sm">{cluster.suggested_text}</p>
                  {cluster.rationale && <p className="mt-2 text-xs text-slate-600">{cluster.rationale}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span title={role === 'Admin' ? 'Publish rule refinement' : 'Admin only: publishing rule refinements is restricted to Admins'}>
                    <button
                      disabled={role !== 'Admin' || publishing.size > 0 || publishing.has(cluster.rule_id)}
                      onClick={() => publish(cluster)}
                      className={`rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed ${role === 'Admin' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500 border border-slate-300'}`}
                    >
                      {publishing.has(cluster.rule_id) ? 'Publishing…' : 'Publish Updated Rule'}
                    </button>
                    {role !== 'Admin' && <span className="ml-1 text-xs text-slate-500" aria-hidden="true">Admin only</span>}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
