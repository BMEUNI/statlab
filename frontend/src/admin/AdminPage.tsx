import { Activity, FolderOpen, LayoutDashboard, SlidersHorizontal, Users, type LucideIcon } from 'lucide-react';
import { formatRoute, type AdminSection } from '../routes.ts';
import AdminDefaults from './AdminDefaults.tsx';
import AdminOverview from './AdminOverview.tsx';
import AdminPeople from './AdminPeople.tsx';
import AdminProjects from './AdminProjects.tsx';
import AdminRuns from './AdminRuns.tsx';

const SECTIONS: { section: AdminSection; label: string; icon: LucideIcon }[] = [
  { section: 'overview', label: 'Overview', icon: LayoutDashboard },
  { section: 'people', label: 'People', icon: Users },
  { section: 'projects', label: 'Projects', icon: FolderOpen },
  { section: 'runs', label: 'Runs', icon: Activity },
  { section: 'defaults', label: 'Defaults', icon: SlidersHorizontal },
];

export default function AdminPage({ section, meId }: { section: AdminSection; meId: string }) {
  return (
    <div className="admin">
      <header className="admin-head">
        <h1>Admin</h1>
        <p className="secondary-line">Budgets, people and projects. Spend counts the model calls of each run; a run counts against the person who started it.</p>
      </header>
      <div className="admin-body">
        <nav className="admin-nav" aria-label="Admin sections">
          {SECTIONS.map(({ section: target, label, icon: Icon }) => (
            <a key={target} href={formatRoute({ name: 'admin', section: target })} aria-current={target === section ? 'page' : undefined}>
              <Icon className="icon" aria-hidden="true" />{label}
            </a>
          ))}
        </nav>
        <div className="admin-main">
          {section === 'overview' && <AdminOverview />}
          {section === 'people' && <AdminPeople meId={meId} />}
          {section === 'projects' && <AdminProjects />}
          {section === 'runs' && <AdminRuns />}
          {section === 'defaults' && <AdminDefaults />}
        </div>
      </div>
    </div>
  );
}
