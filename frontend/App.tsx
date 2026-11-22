import { useCallback, useEffect, useState } from 'react';
import { FileText, FilePlus2, BookOpen, BarChart3, Feather } from 'lucide-react';
import { api, setApiRole, Role, Submission } from './api';
import SubmissionReviewView from './pages/SubmissionReviewView';
import NewSubmissionView from './pages/NewSubmissionView';
import BrandKnowledgeView from './pages/BrandKnowledgeView';
import GovernanceAnalyticsView from './pages/GovernanceAnalyticsView';

type Tab = 'review' | 'new' | 'kb' | 'analytics';
const TABS: { id: Tab; label: string; Icon: typeof FileText }[] = [
  { id: 'review', label: 'Review Workspace', Icon: FileText }, { id: 'new', label: 'New Submission', Icon: FilePlus2 },
  { id: 'kb', label: 'Brand Knowledge Base', Icon: BookOpen }, { id: 'analytics', label: 'Governance Analytics', Icon: BarChart3 }];
const ROLES: Role[] = ['Creator', 'Approver', 'Admin'];

export default function App() {
  const [role, setRoleState] = useState<Role>('Creator');
  const [tab, setTab] = useState<Tab>('review');
  const [subs, setSubs] = useState<Submission[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [err, setErr] = useState('');

  const changeRole = (r: Role) => { setApiRole(r); setRoleState(r); };
  const refresh = useCallback(async (preferId?: string) => {
    try {
      const list = await api.listSubmissions(); setSubs(list);
      setActiveId(cur => preferId ?? cur ?? (list.find(s => /Q4 Enterprise Cloud Launch Post/i.test(s.title)) ?? list[0])?.id ?? null);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { setApiRole('Creator'); refresh(); }, [refresh]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
            <header className="sticky top-0 z-30 border-b border-slate-900 bg-slate-900 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-md bg-teal-600 text-white"><Feather size={18} /></span>
            <h1 className="text-base font-semibold leading-tight">Inkloom AI <span className="font-normal text-slate-300">— NovaTech Cloud Workspace</span></h1>
          </div>
          <div role="group" aria-label="Demo role" className="ml-auto flex rounded-full bg-white/10 p-1">
            {ROLES.map(r => (
              <button key={r} onClick={() => changeRole(r)} aria-pressed={role === r}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-300 ${role === r ? 'bg-teal-600 text-slate-900 shadow' : 'text-slate-200 hover:text-white'}`}>{r}</button>))}
          </div>
          <nav className="flex w-full gap-1 overflow-x-auto" aria-label="Sections">
            {TABS.map(({ id, label, Icon }) => (
              <button key={id} onClick={() => setTab(id)} aria-current={tab === id}
                className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium ${tab === id ? 'border-teal-600 text-white' : 'border-transparent text-slate-300 hover:text-white'}`}>
                <Icon size={16} />{label}</button>))}
          </nav>
        </div>
      </header>
      {err && <p className="mx-auto mt-4 max-w-7xl rounded-md bg-red-50 p-3 text-sm text-red-700">{err}</p>}
      <main className="mx-auto max-w-7xl px-4 py-6">
        {tab === 'review' && <SubmissionReviewView role={role} submissions={subs} activeId={activeId} onSelect={setActiveId} onChanged={() => refresh()} onNew={() => setTab('new')} />}
        {tab === 'new' && <NewSubmissionView onCreated={async id => { await refresh(id); setTab('review'); }} />}
        {tab === 'kb' && <BrandKnowledgeView role={role} />}
        {tab === 'analytics' && <GovernanceAnalyticsView role={role} />}
      </main>
    </div>
  );
}
