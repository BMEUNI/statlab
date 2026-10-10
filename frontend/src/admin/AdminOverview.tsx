import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAdminOverview, updateRunLimit } from '../api/client.ts';
import { formatRoute } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import { formatUsd, monthLabel } from './budget.ts';
import LimitField from './LimitField.tsx';

export default function AdminOverview() {
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ['admin', 'overview'], queryFn: ({ signal }) => getAdminOverview(signal) });
  const saveLimit = useMutation({
    mutationFn: ({ id, value }: { id: string; value: number | null }) => updateRunLimit(id, value),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });

  if (overview.isPending) return <p>Loading…</p>;
  if (overview.isError) return <p role="alert">{errorText(overview.error)}</p>;
  const data = overview.data;
  const settings = data.settings;

  return (
    <div>
      <h2 className="admin-title">{monthLabel(data.month)}</h2>
      <dl className="figures">
        <div><dt>Spent this month</dt><dd>{formatUsd(data.month_spent_usd)}</dd></div>
        <div><dt>Spent in total</dt><dd>{formatUsd(data.total_spent_usd)}</dd></div>
        <div>
          <dt>People</dt>
          <dd>{data.users}</dd>
          <dd className="figure-note">{`${data.admins} admin${data.admins === 1 ? '' : 's'}${data.disabled_users > 0 ? `, ${data.disabled_users} disabled` : ''}`}</dd>
        </div>
        <div><dt>Projects</dt><dd>{data.projects}</dd></div>
        <div>
          <dt>Runs</dt>
          <dd>{data.runs}</dd>
          <dd className="figure-note">{`${data.open_runs} open`}</dd>
        </div>
      </dl>

      <h2>Waiting on a budget</h2>
      {saveLimit.isError && <p role="alert">{errorText(saveLimit.error)}</p>}
      {data.blocked_runs.length === 0 ? (
        <p className="help">No run is waiting on a budget.</p>
      ) : (
        <ul className="attention">
          {data.blocked_runs.map((run) => (
            <li className="attention-row" key={run.id}>
              <div>
                <a href={formatRoute({ name: 'run', runId: run.id })}>{run.project_name}</a> <code>{run.id}</code>
                <p>{run.budget.blocked}</p>
              </div>
              <LimitField
                label={`Run limit for ${run.id}`}
                value={run.limit_usd}
                placeholder="Inherited"
                pending={saveLimit.isPending}
                onSave={(value) => saveLimit.mutate({ id: run.id, value })}
              />
            </li>
          ))}
        </ul>
      )}

      <h2>Defaults in use</h2>
      <dl className="defaults-summary">
        <dt>Monthly limit per person</dt>
        <dd>{settings.default_monthly_limit_usd === null ? 'No limit' : formatUsd(settings.default_monthly_limit_usd)}</dd>
        <dt>Lifetime limit per project</dt>
        <dd>{settings.default_project_limit_usd === null ? 'No limit' : formatUsd(settings.default_project_limit_usd)}</dd>
        <dt>Limit per run</dt>
        <dd>{settings.default_run_limit_usd === null ? 'No limit' : formatUsd(settings.default_run_limit_usd)}</dd>
        <dt>Sign-up</dt>
        <dd>{settings.signup_open ? 'Open' : 'Closed'}</dd>
      </dl>
      <a href="#/admin/defaults">Change defaults</a>
    </div>
  );
}
