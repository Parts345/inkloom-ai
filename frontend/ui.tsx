import { Severity, Status } from '../api';
export const sevStyle: Record<Severity, string> = {
  critical: 'bg-red-100 text-red-800 ring-red-300', major: 'bg-amber-100 text-amber-900 ring-amber-300', minor: 'bg-blue-100 text-blue-800 ring-blue-300' };
export const markStyle: Record<Severity, string> = {
  critical: 'bg-red-100 border-b-2 border-red-500', major: 'bg-amber-100 border-b-2 border-amber-500', minor: 'bg-blue-100 border-b-2 border-blue-500' };
const statusStyle: Record<Status, string> = {
  Draft: 'bg-slate-100 text-slate-700',
  'In Review': 'bg-sky-100 text-sky-800', 'Revision Required': 'bg-amber-100 text-amber-900',
  Approved: 'bg-emerald-100 text-emerald-800', Rejected: 'bg-red-100 text-red-800' };
export const SeverityBadge = ({ s }: { s: Severity }) => <span className={`rounded-full px-2 py-0.5 text-xs font-semibold capitalize ring-1 ${sevStyle[s]}`}>{s}</span>;
export const StatusPill = ({ s }: { s: Status }) => <span className={`rounded-full px-3 py-1 text-xs font-semibold ${statusStyle[s]}`}>{s}</span>;
export const Card = ({ children, className = '' }: { children: React.ReactNode; className?: string }) =>
  <div className={`rounded-lg border border-slate-200 bg-white ${className}`}>{children}</div>;
export const Spinner = ({ label = 'Loading…' }: { label?: string }) => <p className="p-8 text-center text-sm text-slate-500" role="status">{label}</p>;
export const ErrorNote = ({ msg }: { msg: string }) => <p className="m-4 rounded-md bg-red-50 p-3 text-sm text-red-700" role="alert">{msg}</p>;
export const fmt = (d?: string | null) => (d ? new Date(d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—');
