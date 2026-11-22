import { useCallback, useEffect, useState } from 'react';
import { api, ApprovedAsset, AssetType, KbItem, Role } from '../api';
import { Card, ErrorNote, Spinner, fmt } from './ui';

const TABS = [
  { id: 'guideline', label: 'Approved Guidelines' },
  { id: 'fact', label: 'Verified Facts' },
  { id: 'term', label: 'Preferred / Banned Terms' },
  { id: 'asset', label: 'Approved Assets' },
] as const;
export default function BrandKnowledgeView({ role }: { role: Role }) {
  const [items, setItems] = useState<KbItem[] | null>(null);
  const [assets, setAssets] = useState<ApprovedAsset[] | null>(null);
  const [tab, setTab] = useState<'guideline' | 'fact' | 'term' | 'asset'>('guideline');
  const [err, setErr] = useState('');
  const [f, setF] = useState({ title: '', body: '', owner: '', expires_at: '', preferred: '', banned: '' });
  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [assetPreview, setAssetPreview] = useState<string | null>(null);
  const [assetMeta, setAssetMeta] = useState({ name: '', asset_type: 'Logo' as AssetType, usage_guidelines: '' });
  const [assetBusy, setAssetBusy] = useState(false);
  const [assetErr, setAssetErr] = useState('');
  const [assetOk, setAssetOk] = useState('');

  const load = () => api.listKb().then(setItems).catch(e => setErr(e.message));
  const loadAssets = () => api.listAssets().then(setAssets).catch(e => setAssetErr(e.message));
  useEffect(() => { load(); }, []);

  const add = async () => {
    try {
      await api.addKb({ kind: tab as KbItem['kind'], ...f, expires_at: f.expires_at || null });
      setF({ title: '', body: '', owner: '', expires_at: '', preferred: '', banned: '' });
      setErr(''); await load();
    } catch (e) { setErr((e as Error).message); }
  };

  const handleFile = (file: File | null) => {
    setAssetFile(file); setAssetErr(''); setAssetOk('');
    if (!file) { setAssetPreview(null); setAssetMeta({ name: '', asset_type: 'Logo', usage_guidelines: '' }); return; }
    if (!/\.(png|jpe?g)$/i.test(file.name)) return setAssetErr('Only .png, .jpg or .jpeg images are allowed.');
    const url = URL.createObjectURL(file);
    setAssetPreview(url);
    setAssetMeta({ name: file.name.replace(/\.[^.]+$/, ''), asset_type: 'Logo', usage_guidelines: '' });
  };

  const revokePreview = useCallback(() => {
    if (assetPreview) { URL.revokeObjectURL(assetPreview); setAssetPreview(null); }
  }, [assetPreview]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!assetFile) return setAssetErr('Choose an image file.');
    if (!/\.(png|jpe?g)$/i.test(assetFile.name)) return setAssetErr('Only .png, .jpg or .jpeg images are allowed.');
    if (!assetMeta.usage_guidelines.trim()) return setAssetErr('Usage guidelines are required.');
    setAssetBusy(true); setAssetErr(''); setAssetOk('');
    try {
      await api.uploadAsset(assetFile, { name: assetMeta.name, asset_type: assetMeta.asset_type, usage_guidelines: assetMeta.usage_guidelines });
      setAssetOk('Asset uploaded successfully.');
      setAssetFile(null); setAssetMeta({ name: '', asset_type: 'Logo', usage_guidelines: '' });
      revokePreview();
      await loadAssets();
    } catch (e) { setAssetErr((e as Error).message); } finally { setAssetBusy(false); }
  };

  const canUpload = role === 'Approver' || role === 'Admin';
  const rows = (items ?? []).filter(i => i.kind === tab);
  const assetRows = assets ?? [];
  const inp = (k: keyof typeof f, ph: string, type = 'text') => <input type={type} value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} placeholder={ph} aria-label={ph} className="rounded-md border border-slate-300 px-3 py-2 text-sm" />;
  const assetInp = (k: keyof typeof assetMeta, ph: string, type = 'text') => <input type={type} value={assetMeta[k]} onChange={e => setAssetMeta({ ...assetMeta, [k]: e.target.value })} placeholder={ph} aria-label={ph} className="rounded-md border border-slate-300 px-3 py-2 text-sm" disabled={!canUpload} />;

  return (
    <div className="space-y-4">
      <div role="tablist" className="flex gap-1 border-b border-slate-200">
        {TABS.map(t => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
          className={`border-b-2 px-4 py-2 text-sm font-medium ${tab === t.id ? 'border-teal-600 text-teal-800' : 'border-transparent text-slate-500'}`}>{t.label}</button>)}
      </div>
      {err && <ErrorNote msg={err} />}

      {tab === 'asset' && (
        <>
          {!assets ? <Spinner /> : (
            <div>
              <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                {assetRows.length === 0 && <Card className="p-10 text-center text-sm text-slate-500 col-span-full">No approved assets yet.</Card>}
                {assetRows.map(a => (
                  <Card key={a.id} className="p-3">
                    <img src={a.image_url} alt={a.name} className="w-full h-24 rounded object-cover border border-slate-200" />
                    <p className="mt-2 text-sm font-medium" title={a.name}>{a.name}</p>
                    <span className="inline-block mt-1 rounded-full border border-slate-200 px-2 py-0.5 text-xs text-slate-600">{a.asset_type}</span>
                    <p className="mt-2 text-sm text-slate-600">{a.usage_guidelines}</p>
                    <p className="mt-2 text-xs text-slate-500">Added by {a.uploaded_by ?? '—'} · {fmt(a.uploaded_at)}</p>
                  </Card>
                ))}
              </div>
              <Card className="mt-6 p-4">
                <h3 className="mb-2 text-sm font-semibold">Upload Approved Asset</h3>
                {!canUpload ? <p className="text-sm text-slate-500">Switch to Approver or Admin to upload approved assets.</p> : (
                  <form onSubmit={handleSubmit} className="space-y-3">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-2">
                        <label className="block text-xs font-medium text-slate-700">Image</label>
                        <input type="file" accept=".png,.jpg,.jpeg" onChange={e => handleFile(e.target.files?.[0] ?? null)}
                          aria-label="Image file" className="block w-full text-sm text-slate-600 file:mr-3 file:py-1 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100 disabled:opacity-50 disabled:cursor-not-allowed" disabled={assetBusy} />
                        {assetPreview && <img src={assetPreview} alt={`Preview of ${assetMeta.name || 'selected image'}`} className="h-24 rounded border border-slate-200 object-cover" />}
                        {assetErr && <p className="text-sm text-red-700" role="alert">{assetErr}</p>}
                        <button type="button" onClick={() => { handleFile(null); revokePreview(); }} aria-label="Remove selected image" className="text-xs underline underline-offset-4 text-red-700 hover:text-red-900">Remove image</button>
                      </div>
                      <div className="space-y-2">
                        {assetInp('name', 'Asset name')}
                        <select value={assetMeta.asset_type} onChange={e => setAssetMeta({ ...assetMeta, asset_type: e.target.value as AssetType })}
                          aria-label="Asset type" className="rounded-md border border-slate-300 px-3 py-2 text-sm" disabled={assetBusy}>
                          {(['Logo', 'Banner', 'Style Reference'] as const).map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <textarea value={assetMeta.usage_guidelines} onChange={e => setAssetMeta({ ...assetMeta, usage_guidelines: e.target.value })}
                          aria-label="Usage guidelines" rows={3} className="rounded-md border border-slate-300 px-3 py-2 text-sm" disabled={assetBusy} placeholder="Usage guidelines…"></textarea>
                      </div>
                    </div>
                    {assetOk && <p className="m-2 rounded-md bg-emerald-50 p-2 text-sm text-emerald-800" role="status">{assetOk}</p>}
                    <button type="submit" disabled={assetBusy || !assetFile || !assetMeta.usage_guidelines.trim()}
                      className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed">{assetBusy ? 'Uploading…' : 'Upload Approved Asset'}</button>
                  </form>
                )}
              </Card>
            </div>
          )}
        </>
      )}

      {tab !== 'asset' && items && (
        <>
          <Card className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-slate-600"><tr>
                {tab === 'term' ? <><th className="p-3">Preferred</th><th>Banned</th><th>Note</th></> : <><th className="p-3">Title</th><th>Content</th><th>Owner</th>{tab === 'fact' && <th>Expiry</th>}</>}</tr></thead>
              <tbody>{rows.map(r => <tr key={r.id} className="border-t border-slate-100 align-top">
                {tab === 'term' ? <><td className="p-3 font-medium text-emerald-800">{r.preferred}</td><td className="text-red-700 line-through">{r.banned}</td><td>{r.body}</td></>
                  : <><td className="p-3 font-medium">{r.title}</td><td className="max-w-md py-3 pr-3">{r.body}</td><td>{r.owner}</td>
                    {tab === 'fact' && <td><span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${r.expired ? 'bg-red-100 text-red-800' : 'bg-emerald-100 text-emerald-800'}`}>{r.expired ? 'Expired' : 'Valid'} · {fmt(r.expires_at)}</span></td>}</>}
              </tr>)}</tbody>
              {!rows.length && <p className="p-6 text-center text-sm text-slate-500">Nothing here yet.</p>}
            </table>
          </Card>
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
        </>
      )}
      {!items && tab !== 'asset' && <Spinner />}
    </div>
  );
}
