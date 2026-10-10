import { useMutation, useQuery } from '@tanstack/react-query';
import { LogOut, ShieldCheck } from 'lucide-react';
import { getMyBudget, signOut } from '../api/client.ts';
import type { AuthUser } from '../api/types.ts';
import BudgetMeter from '../ui/BudgetMeter.tsx';
import { errorText } from '../ui/format.ts';
import { initials } from '../admin/budget.ts';

export default function UserMenu({ user, onSignedOut }: { user: AuthUser; onSignedOut: () => void }) {
  const budget = useQuery({ queryKey: ['auth', 'budget'], queryFn: ({ signal }) => getMyBudget(signal) });
  const logout = useMutation({
    mutationFn: () => signOut(),
    onSuccess: () => onSignedOut(),
  });

  return (
    <details className="user-menu">
      <summary aria-label={`Account: ${user.name}`}>
        <span className="avatar" aria-hidden="true">{initials(user.name)}</span>
        <span className="user-name">{user.name}</span>
      </summary>
      <div className="help-pop user-pop">
        <p className="user-pop-name">{user.name}</p>
        <p className="help">{user.email}</p>
        <p className="help-pop-title">This month</p>
        {budget.isPending && <p>Loading…</p>}
        {budget.isError && <p role="alert">{errorText(budget.error)}</p>}
        {budget.isSuccess && <BudgetMeter meter={budget.data} label="Your spend this month" />}
        {user.is_admin && (
          <a className="button" href="#/admin"><ShieldCheck className="icon" aria-hidden="true" />Admin panel</a>
        )}
        <button type="button" onClick={() => logout.mutate()} disabled={logout.isPending}>
          <LogOut className="icon" aria-hidden="true" />Sign out
        </button>
      </div>
    </details>
  );
}
