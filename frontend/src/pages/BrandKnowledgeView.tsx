import { useEffect, useState } from 'react';
import { api, KbItem, Role } from '../api';
import { Card, ErrorNote, Spinner, fmt } from './ui';

const TABS = [{ id: 'guideline', label: 'Approved Guidelines' }, { id: 'fact', label: 'Verified Facts' }, { id: 'term', label: 'Preferred / Banned Terms' }] as const;
export default function BrandKnowledgeView({ role }: { role: Role }) {
  const [items, setItems] = useState<KbItem[] | null>(null); const [tab, setTab] = useState<KbItem['kind']>('guideline'); const [err, setErr] = useState('');
  const [f, setF] = useState({ title: '', body: '', owner: '', expires_at: '', preferred: '', banned: '' });
  const load = () => api.listKb().then(setItems).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);
  const add = async () => {
    try { await api.addKb({ kind: tab, ...f, expires_at: f.expires_at || null }); setF({ title: '', body: '', owner: '', expires_at: '', preferred: '', banned: '' }); setErr(''); await load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const rows = (items ?? []).filter(i => i.kind === tab);
  const inp = (k: keyof typeof f, ph: string, type = 'text') => <input type={type} value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} placeholder={ph} aria-label={ph} className="rounded-md border border-slate-300 px-3 py-2 text-sm" />;
  return (
    <div className="space-y-4">
      <div role="tablist" className="flex gap-1 border-b border-slate-200">
        {TABS.map(t => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
          className={`border-b-2 px-4 py-2 text-sm font-medium ${tab === t.id ? 'border-teal-600 text-teal-800' : 'border-transparent text-slate-500'}`}>{t.label}</button>)}
      </div>
      {err && <ErrorNote msg={err} />}
      {!items ? <Spinner /> : (
        <Card className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600"><tr>
              {tab === 'term' ? <><th className="p-3">Preferred</th><th>Banned</th><th>Note</th></> : <><th className="p-3">Title</th><th>Content</th><th>Owner</th>{tab === 'fact' && <th>Expiry</th>}</>}</tr></thead>
            <tbody>{rows.map(r => <tr key={r.id} className="border-t border-slate-100 align-top">
              {tab === 'term' ? <><td className="p-3 font-medium text-emerald-800">{r.preferred}</td><td className="text-red-700 line-through">{r.banned}</td><td>{r.body}</td></>
                : <><td className="p-3 font-medium">{r.title}</td><td className="max-w-md py-3 pr-3">{r.body}</td><td>{r.owner}</td>
                  {tab === 'fact' && <td><span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${r.expired ? 'bg-red-100 text-red-800' : 'bg-emerald-100 text-emerald-800'}`}>{r.expired ? 'Expired' : 'Valid'} · {fmt(r.expires_at)}</span></td>}</>}
            </tr>)}</tbody>
          </table>
          {!rows.length && <p className="p-6 text-center text-sm text-slate-500">Nothing here yet.</p>}
        </Card>)}
      <Card className="p-4">
        <h3 className="mb-2 text-sm font-semibold">Add to this list</h3>
        {role !== 'Admin' ? <p className="text-sm text-slate-500">Switch to Admin to add rules, facts or terms.</p> : (
          <div className="grid gap-2 sm:grid-cols-2">
            {tab === 'term' ? <>{inp('preferred', 'Preferred term')}{inp('banned', 'Banned term')}</> : <>{inp('title', 'Title')}{inp('owner', 'Owner')}</>}
            <input value={f.body} onChange={e => setF({ ...f, body: e.target.value })} placeholder={tab === 'term' ? 'Note' : 'Rule or fact text'} className="rounded-md border border-slate-300 px-3 py-2 text-sm sm:col-span-2" />
            {tab === 'fact' && inp('expires_at', 'Expiry date', 'date')}
            <button onClick={add} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white sm:col-span-2">Add entry</button>
          </div>)}
      </Card>
    </div>
  );
}
