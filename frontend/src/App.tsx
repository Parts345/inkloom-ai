import { useEffect, useState } from "react";

type Role = "creator" | "approver" | "admin";
const ROLES: { role: Role; label: string }[] = [
  { role: "creator", label: "Creator" },
  { role: "approver", label: "Approver" },
  { role: "admin", label: "Admin" },
];

export default function App() {
  const [role, setRole] = useState<Role>("creator");
  const [health, setHealth] = useState<string>("checking…");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => setHealth(d.status))
      .catch(() => setHealth("unreachable"));
  }, []);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex items-center justify-between border-b bg-white px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold">Inkloom AI</h1>
          <p className="text-xs text-slate-500">NovaTech Cloud workspace · API {health}</p>
        </div>
        <nav className="flex gap-1 rounded-lg bg-slate-100 p-1" aria-label="Demo role">
          {ROLES.map((r) => (
            <button
              key={r.role}
              onClick={() => setRole(r.role)}
              aria-pressed={role === r.role}
              className={`rounded-md px-3 py-1 text-sm ${
                role === r.role ? "bg-white shadow font-medium" : "text-slate-600"
              }`}
            >
              {r.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="mx-auto max-w-3xl p-6">
        <p className="text-sm text-slate-600">
          Acting as <strong>{role}</strong>. Submission and review screens are built in Round 2.
        </p>
      </main>
    </div>
  );
}
