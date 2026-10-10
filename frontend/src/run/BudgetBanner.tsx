import { useQuery } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import { getRunBudget } from '../api/client.ts';
import type { RunStatus } from '../api/types.ts';
import { isOpenStatus } from '../admin/budget.ts';

export default function BudgetBanner({ runId, status, isAdmin }: { runId: string; status: RunStatus; isAdmin: boolean }) {
  const budget = useQuery({
    queryKey: ['budget', runId, status],
    queryFn: ({ signal }) => getRunBudget(runId, signal),
  });
  const blocked = budget.data?.blocked;
  if (!isOpenStatus(status) || typeof blocked !== 'string') return null;
  return (
    <div className="budget-banner" role="status">
      <TriangleAlert className="icon" aria-hidden="true" />
      <div>
        <p><strong>{status === 'paused' ? 'Paused by a budget.' : 'Budget limit reached.'}</strong>{` ${blocked}`}</p>
        <p className="help">
          {isAdmin ? (
            <>Raise the limit in the <a href="#/admin/runs">admin panel</a>, then resume the run.</>
          ) : 'Ask an admin to raise the limit, then resume the run.'}
        </p>
      </div>
    </div>
  );
}
