import { useEffect, useState } from 'react';
import { api, Analytics } from '../api';
import { Card, ErrorNote, Spinner } from './ui';

function Bars({ rows, color }: { rows: { label: string; n: number }[]; color: string }) {
  const max = Math.max(1, ...rows.map(r => r.n));
  return <ul className="space-y-2">{rows.map(r => <li key={r.label} className="text-sm">
    <div className="flex justify-between"><span className="truncate pr-2">{r.label}</span><b>{r.n}</b></div>
    <div className="h-2 rounded bg-slate-100"><div className={`h-2 rounded ${color}`} style={{ width: `${(r.n / max) * 100}%` }} /></div></li>)}
    {!rows.length && <li className="text-sm text-slate-500">No data yet.</li>}</ul>;
}
export default function GovernanceAnalyticsView() {
  const [a, setA] = useState<Analytics | null>(null); const [err, setErr] = useState('');
  useEffect(() => { api.analytics().then(setA).catch(e => setErr(e.message)); }, []);
  if (err) return <ErrorNote msg={err} />; if (!a) return <Spinner />;
  const rate = a.approval_rate <= 1 ? a.approval_rate * 100 : a.approval_rate;
  return (
    <div className="space-y-4">
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
    </div>
  );
}
