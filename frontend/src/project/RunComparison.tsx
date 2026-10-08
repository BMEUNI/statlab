import { useQueries } from '@tanstack/react-query';
import { getRun } from '../api/client.ts';
import { champion, type Champion } from '../report/champion.ts';
import { formatMetric } from '../run/compare.ts';
import { nodeLabel, parseRunView, STATUS_LABELS } from '../run/model.ts';
import { formatRoute } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import { forkTarget } from './lineage.ts';

export default function RunComparison({ runIds }: { runIds: string[] }) {
  const results = useQueries({
    queries: runIds.map((id) => ({ queryKey: ['run', id], queryFn: ({ signal }: { signal: AbortSignal }) => getRun(id, signal) })),
  });
  const failed = results.find((result) => result.isError);
  if (failed?.error != null) return <p role="alert">{errorText(failed.error)}</p>;
  if (results.some((result) => result.isPending)) return <p>Loading runs…</p>;
  const cells = results.flatMap((result) => {
    if (result.data === undefined) return [];
    const view = parseRunView(result.data.state);
    return [{ run: result.data.run, best: view === null ? null : champion(view) }];
  });
  const primary = cells.find((cell) => cell.best?.primaryMetric != null)?.best?.primaryMetric ?? null;
  const names = [...new Set(cells.flatMap((cell) => Object.keys(cell.best?.testMetrics ?? {})))].sort();
  const ordered = primary !== null && names.includes(primary) ? [primary, ...names.filter((name) => name !== primary)] : names;
  const validation = (best: Champion | null): string =>
    best?.validation != null && best.primaryMetric === primary ? formatMetric(best.validation) : '—';
  const row = (label: string, value: (cell: (typeof cells)[number]) => string) => (
    <tr key={label}><th scope="row">{label}</th>{cells.map((cell) => <td key={cell.run.id}>{value(cell)}</td>)}</tr>
  );
  return (
    <div className="table-wrap">
      <table>
        <caption>Run comparison</caption>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            {cells.map(({ run }) => {
              const target = forkTarget(run);
              return (
                <th scope="col" key={run.id}>
                  <a href={formatRoute({ name: 'run', runId: run.id })}>{run.id}</a>
                  {target !== null && <small className="help">Rewound at {nodeLabel(target)}</small>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {row('Status', (cell) => STATUS_LABELS[cell.run.status])}
          {row('Champion', (cell) => (cell.best === null ? '—' : `${cell.best.label} (${cell.best.candidateId})`))}
          {row('Estimator', (cell) => cell.best?.estimator ?? '—')}
          {row(`Validation ${primary ?? ''}`.trim(), (cell) => validation(cell.best))}
          {ordered.map((name) => row(`Test ${name}`, (cell) => {
            const metric = cell.best?.testMetrics[name];
            return metric === undefined ? '—' : formatMetric(metric);
          }))}
        </tbody>
      </table>
    </div>
  );
}
