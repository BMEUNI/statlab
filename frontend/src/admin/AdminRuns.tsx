import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listAdminRuns, updateRunLimit } from '../api/client.ts';
import { formatRoute } from '../routes.ts';
import { errorText, formatTime, statusPillClass } from '../ui/format.ts';
import BudgetMeter from '../ui/BudgetMeter.tsx';
import { isOpenStatus } from './budget.ts';
import LimitField from './LimitField.tsx';

type Filter = 'all' | 'open' | 'blocked';

export default function AdminRuns() {
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ['admin', 'runs'], queryFn: ({ signal }) => listAdminRuns(signal) });
  const [filter, setFilter] = useState<Filter>('all');
  const saveLimit = useMutation({
    mutationFn: ({ id, value }: { id: string; value: number | null }) => updateRunLimit(id, value),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });

  const chips: { value: Filter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'open', label: 'Open' },
    { value: 'blocked', label: 'Waiting on a budget' },
  ];

  return (
    <div>
      <div className="chips">
        {chips.map((chip) => (
          <button key={chip.value} type="button" className="chip" aria-pressed={filter === chip.value} onClick={() => setFilter(chip.value)}>
            {chip.label}
          </button>
        ))}
      </div>
      <p className="help">The latest 200 runs. A run paused by a budget continues after you raise the limit and its owner resumes it.</p>
      {saveLimit.isError && <p role="alert">{errorText(saveLimit.error)}</p>}
      {runs.isPending && <p>Loading…</p>}
      {runs.isError && <p role="alert">{errorText(runs.error)}</p>}
      {runs.isSuccess && (() => {
        const shown = runs.data.filter((run) => {
          if (filter === 'open') return isOpenStatus(run.status);
          if (filter === 'blocked') return run.budget.blocked !== null && isOpenStatus(run.status);
          return true;
        });
        if (shown.length === 0) return <p>No runs match.</p>;
        return (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Runs</caption>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Project</th>
                  <th>Started by</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Run spend</th>
                  <th>Run limit</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <a href={formatRoute({ name: 'run', runId: run.id })}><code>{run.id}</code></a>
                      {run.budget.blocked !== null && <p className="row-note">{run.budget.blocked}</p>}
                    </td>
                    <td>{run.project_name}</td>
                    <td>{run.budget.started_by ?? '—'}</td>
                    <td><span className={statusPillClass(run.status)}>{run.status}</span></td>
                    <td>{formatTime(run.started_at)}</td>
                    <td><BudgetMeter compact meter={run.budget.run} label={`Spend of run ${run.id}`} /></td>
                    <td>
                      <LimitField
                        label={`Run limit for ${run.id}`}
                        value={run.limit_usd}
                        placeholder="Inherited"
                        pending={saveLimit.isPending}
                        onSave={(value) => saveLimit.mutate({ id: run.id, value })}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })()}
    </div>
  );
}
