import { useCallback, useEffect, useState } from 'react';
import { api, Analytics, DisputedRuleCluster, Role } from '../api';
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
  const [clusters, setClusters] = useState<DisputedRuleCluster[] | null>(null);
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState('');
  const [busyRule, setBusyRule] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [analytics, disputed] = await Promise.all([api.analytics(), api.disputedRules()]);
      setA(analytics);
      setClusters(disputed);
      setErr('');
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const apply = async (c: DisputedRuleCluster) => {
    setBusyRule(c.rule_id); setErr(''); setNotice('');
    try {
      const result = await api.applyRefinement(c.rule_id, c.proposed_rule_text);
      setNotice(`"${result.title}" is now v${result.new_version}.${result.reindexed ? ' The Knowledge Base was re-indexed.' : ''}`);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusyRule(null);
    }
  };

  if (!a || !clusters) return err ? <ErrorNote msg={err} /> : <Spinner />;
  const rate = a.approval_rate <= 1 ? a.approval_rate * 100 : a.approval_rate;

  return (
    <div className="space-y-4">
      {err && <ErrorNote msg={err} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="p-5"><p className="text-sm text-slate-500">Total submissions</p><p className="text-3xl font-semibold">{a.total_submissions}</p></Card>
        <Card className="p-5"><p className="text-sm text-slate-500">Approval rate</p><p className="text-3xl font-semibold">{rate.toFixed(0)}%</p>
          <div className="mt-2 h-2 rounded bg-slate-100"><div className="h-2 rounded bg-emerald-500" style={{ width: `${rate}%` }} /></div></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5"><h3 className="mb-3 font-semibold">Top violated rules</h3><Bars color="bg-red-500" rows={a.top_rules.map(r => ({ label: r.rule, n: r.count }))} /></Card>
        <Card className="p-5"><h3 className="mb-3 font-semibold">Issues by category</h3><Bars color="bg-teal-600" rows={a.by_category.map(r => ({ label: r.category, n: r.count }))} /></Card>
        <Card className="p-5"><h3 className="mb-1 font-semibold">Disputed rules needing clarification</h3><p className="mb-3 text-xs text-slate-500">Rules reviewers keep disputing may be unclear or outdated.</p>
          <Bars color="bg-amber-500" rows={a.disputed_rules.map(r => ({ label: r.rule, n: r.disputes }))} /></Card>
      </div>
      <Card className="p-5">
        <h3 className="font-semibold">Dispute-driven rule refinement</h3>
        <p className="mb-4 text-xs text-slate-500">Creator disputes are clustered by rule. Each cluster proposes an updated rule. Admins review it and apply it with one click.</p>
        {notice && <p role="status" className="mb-4 rounded-md bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}
        {!clusters.length ? <p className="text-sm text-slate-500">No open disputes. Rules that creators dispute will appear here.</p> : (
          <div className="space-y-4">
            {clusters.map(c => (
              <div key={c.rule_id} className="space-y-3 rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="font-semibold">{c.rule_title}</h4>
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">{c.dispute_count} dispute{c.dispute_count === 1 ? '' : 's'}</span>
                  <span className="text-xs text-slate-500">v{c.rule_version} · {c.severity}</span>
                  {c.diagnosis && (
                    <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-semibold text-sky-800">
                      {({ ambiguous: 'Ambiguous wording', overly_broad: 'Overly broad', outdated: 'Possibly outdated', well_calibrated: 'Well calibrated' } as Record<string, string>)[c.diagnosis] ?? c.diagnosis}
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-700"><b>Why creators dispute it:</b> {c.cluster_summary}</p>
                {c.reason_themes && c.reason_themes.length > 0 && (
                  <p className="text-xs text-slate-500"><b>Dispute themes:</b> {c.reason_themes.join(' · ')}</p>
                )}
                <ul className="space-y-1 text-sm text-slate-600">
                  {c.disputes.slice(0, 3).map(d => <li key={d.finding_id}>“{d.dispute_reason}” <span className="text-slate-400">({d.submission_title})</span></li>)}
                </ul>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-md bg-slate-50 p-3 text-sm"><p className="mb-1 text-xs font-semibold uppercase text-slate-500">Current rule</p>{c.current_rule_text}</div>
                  <div className="rounded-md bg-emerald-50 p-3 text-sm"><p className="mb-1 text-xs font-semibold uppercase text-emerald-700">Proposed rule</p>{c.proposed_rule_text}</div>
                </div>
                {role === 'Admin' ? (
                  <button onClick={() => apply(c)} disabled={busyRule === c.rule_id}
                    className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
                    {busyRule === c.rule_id ? 'Applying…' : 'Apply Suggested Rule'}</button>
                ) : <p className="text-sm text-slate-500">Switch to Admin to apply this rule update.</p>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
