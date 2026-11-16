import { useRef, useState } from 'react';
import { UploadCloud, Wand2 } from 'lucide-react';
import { api, SAMPLE_DRAFT } from '../api';
import { Card } from './ui';

const ACCEPT = '.txt,.docx,.pdf,.png,.jpg,.jpeg';
export default function NewSubmissionView({ onCreated }: { onCreated: (id: string) => void }) {
  const [title, setTitle] = useState(''); const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null); const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState('');
  const input = useRef<HTMLInputElement>(null);

  const pick = (f?: File) => {
    if (!f) return;
    if (!/\.(txt|docx|pdf|png|jpe?g)$/i.test(f.name)) return setErr('Use a .txt, .docx, .pdf, .png or .jpg file.');
    setErr(''); setFile(f); if (!title) setTitle(f.name.replace(/\.[^.]+$/, ''));
  };
  const submit = async () => {
    setBusy(true); setErr('');
    try { const s = file ? await api.createFile(file, title || file.name) : await api.createText(title || 'Untitled draft', text); onCreated(s.id); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div><h2 className="text-xl font-semibold">New submission</h2><p className="text-sm text-slate-500">Upload a draft or paste text. Inkloom checks it against brand rules and verified facts.</p></div>
      <Card className="space-y-4 p-5">
        <label className="block text-sm font-medium">Title
          <input value={title} onChange={e => setTitle(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" placeholder="e.g. Q4 launch announcement" /></label>
        <div onDragOver={e => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={e => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}
          onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={e => e.key === 'Enter' && input.current?.click()}
          className={`grid cursor-pointer place-items-center rounded-lg border-2 border-dashed p-8 text-center ${over ? 'border-teal-500 bg-teal-50' : 'border-slate-300 hover:bg-slate-50'}`}>
          <UploadCloud className="text-slate-400" />
          <p className="mt-2 text-sm">{file ? <b>{file.name}</b> : 'Drop a file here, or click to browse'}</p>
          <p className="text-xs text-slate-500">.txt  .docx  .pdf  .png  .jpg</p>
          <input ref={input} type="file" accept={ACCEPT} hidden onChange={e => pick(e.target.files?.[0])} />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between"><label htmlFor="paste" className="text-sm font-medium">Or paste text</label>
            <button onClick={() => { setText(SAMPLE_DRAFT); setFile(null); setTitle('Sample draft with violations'); }}
              className="flex items-center gap-1 rounded-md bg-teal-50 px-3 py-1.5 text-sm font-medium text-teal-800 hover:bg-teal-100"><Wand2 size={14} />Load Sample Draft with Violations</button></div>
          <textarea id="paste" value={text} onChange={e => { setText(e.target.value); setFile(null); }} rows={7} className="w-full rounded-md border border-slate-300 p-3 text-sm" placeholder="Paste your draft…" />
        </div>
        {err && <p className="text-sm text-red-700" role="alert">{err}</p>}
        <button disabled={busy || (!file && !text.trim())} onClick={submit}
          className="rounded-md bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-40">{busy ? 'Checking draft…' : 'Submit for review'}</button>
      </Card>
    </div>
  );
}
