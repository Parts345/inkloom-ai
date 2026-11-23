import { useEffect, useState, type FormEvent } from 'react';
import { api, ApprovedAsset, KbItem, Role } from '../api';
import { Card, ErrorNote, Spinner, fmt } from './ui';

const TABS = [{ id: 'guideline', label: 'Approved Guidelines' }, { id: 'fact', label: 'Verified Facts' }, { id: 'term', label: 'Preferred / Banned Terms' }] as const;
const ASSET_TYPES = [{ id: 'logo', label: 'Logo' }, { id: 'banner', label: 'Banner' }, { id: 'palette', label: 'Palette' }] as const;

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
      <ApprovedAssets role={role} />
    </div>
  );
}

function ApprovedAssets({ role }: { role: Role }) {
  const [assets, setAssets] = useState<ApprovedAsset[] | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState('');
  const [assetType, setAssetType] = useState<(typeof ASSET_TYPES)[number]['id']>('logo');
  const [rules, setRules] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const canUpload = role !== 'Creator';

  const load = () => api.listAssets().then(setAssets).catch(e => setErr((e as Error).message));
  useEffect(() => { load(); }, []);

  const upload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!file) { setErr('Choose a PNG or JPG file first.'); return; }
    const data = new FormData();
    data.append('title', title.trim());
    data.append('asset_type', assetType);
    data.append('usage_rules', rules.trim());
    data.append('file', file);
    setBusy(true); setErr('');
    try {
      await api.uploadAsset(data);
      setTitle(''); setRules(''); setFile(null); setFileKey(k => k + 1);
      await load();
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-4 p-4">
      <h3 className="text-sm font-semibold">Approved visual assets</h3>
      <p className="text-xs text-slate-500">Logos and banners that campaign images are checked against.</p>
      {err && <ErrorNote msg={err} />}
      {!assets ? <Spinner label="Loading assets…" /> : assets.length === 0 ? (
        <p className="text-sm text-slate-500">No approved assets yet.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {assets.map(a => (
            <figure key={a.id} className="rounded-lg border border-slate-200 p-3">
              <img src={a.image_url} alt={a.title} className="mb-2 h-32 w-full rounded bg-slate-50 object-contain" />
              <figcaption className="text-sm">
                <b>{a.title}</b> <span className="text-xs text-slate-500">{a.asset_type} · v{a.version}</span>
                <p className="mt-1 text-xs text-slate-600">{a.usage_rules}</p>
              </figcaption>
            </figure>
          ))}
        </div>
      )}
      {canUpload ? (
        <form onSubmit={upload} className="grid gap-2 border-t border-slate-200 pt-4 sm:grid-cols-2">
          <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Asset name" aria-label="Asset name" required className="rounded-md border border-slate-300 px-3 py-2 text-sm" />
          <select value={assetType} onChange={e => setAssetType(e.target.value as (typeof ASSET_TYPES)[number]['id'])} aria-label="Asset type" className="rounded-md border border-slate-300 px-3 py-2 text-sm">
            {ASSET_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
          <textarea value={rules} onChange={e => setRules(e.target.value)} placeholder="Usage rules, e.g. top-left, minimum 24px clear space" aria-label="Usage rules" required rows={2} className="rounded-md border border-slate-300 px-3 py-2 text-sm sm:col-span-2" />
          <input key={fileKey} type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" onChange={e => setFile(e.target.files?.[0] ?? null)} aria-label="Asset image" className="text-sm sm:col-span-2" />
          <button type="submit" disabled={busy} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 sm:col-span-2">{busy ? 'Uploading…' : 'Upload approved asset'}</button>
        </form>
      ) : <p className="border-t border-slate-200 pt-4 text-sm text-slate-500">Switch to Approver or Admin to upload approved assets.</p>}
    </Card>
  );
}
