import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getProject, listContracts, listDatasets } from '../api/client.ts';
import type { RunStatus } from '../api/types.ts';
import { getAt } from '../contract/draft.ts';
import RunComparison from '../project/RunComparison.tsx';
import { lineage } from '../project/lineage.ts';
import { modeLabel, nodeLabel } from '../run/model.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText, formatTime } from '../ui/format.ts';

function pillClass(status: RunStatus): string {
  if (status === 'awaiting_user' || status === 'paused') return 'pill gate';
  if (status === 'failed' || status === 'cancelled') return 'pill fault';
  return status === 'completed' ? 'pill signal' : 'pill';
}

function runLink(runId: string | null) {
  return runId === null ? '—' : <a href={formatRoute({ name: 'run', runId })}>{runId}</a>;
}

export default function ProjectPage({ projectId, navigate }: { projectId: string; navigate: (route: Route) => void }) {
  const project = useQuery({ queryKey: ['project', projectId], queryFn: ({ signal }) => getProject(projectId, signal) });
  const datasets = useQuery({ queryKey: ['datasets', projectId], queryFn: ({ signal }) => listDatasets(projectId, signal) });
  const contracts = useQuery({ queryKey: ['contracts', projectId], queryFn: ({ signal }) => listContracts(projectId, signal) });

  const [selected, setSelected] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);

  return (
    <>
      {project.isSuccess && (
        <div className="page-head">
          <div>
            <h1>{project.data.project.name}</h1>
            <p className="secondary-line">{project.data.project.slug}</p>
          </div>
          <button type="button" className="primary" onClick={() => navigate({ name: 'setup', projectId })}>New run</button>
        </div>
      )}

      <h2>Runs</h2>
      {project.isPending && <p>Loading runs…</p>}
      {project.isError && <p role="alert">{errorText(project.error)}</p>}
      {project.isSuccess && project.data.runs.length === 0 && <p>No runs yet. Start one with New run.</p>}
      {project.isSuccess && project.data.runs.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Runs</caption>
              <thead>
                <tr><th>Compare</th><th>Run</th><th>Status</th><th>Mode</th><th>Started</th><th>Ended</th><th>Champion</th></tr>
              </thead>
              <tbody>
                {lineage(project.data.runs).map(({ run, depth, forkTarget }) => (
                  <tr key={run.id}>
                    <td>
                      <input
                        type="checkbox" aria-label={`Compare run ${run.id}`} checked={selected.includes(run.id)}
                        onChange={(event) =>
                          setSelected(event.target.checked ? [...selected, run.id] : selected.filter((id) => id !== run.id))}
                      />
                    </td>
                    <td style={{ paddingLeft: `${12 + depth * 24}px` }}>
                      <a href={formatRoute({ name: 'run', runId: run.id })}><code>{run.id}</code></a>
                      {depth > 0 && forkTarget !== null && <small className="help">Rewound at {nodeLabel(forkTarget)}</small>}
                    </td>
                    <td><span className={pillClass(run.status)}>{run.status}</span></td>
                    <td>{modeLabel(run.mode, project.data.switched_runs.includes(run.id))}</td>
                    <td>{formatTime(run.started_at)}</td>
                    <td>{formatTime(run.ended_at)}</td>
                    <td>{run.champion_model_id !== null ? 'Yes' : 'No'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="help">Select two or more runs. A rewound run differs from its parent by one decision.</p>
          <button type="button" disabled={selected.length < 2} onClick={() => setComparing(!comparing)}>
            Compare selected runs
          </button>
          {comparing && selected.length >= 2 && <RunComparison runIds={selected} />}
        </>
      )}

      <h2>Datasets</h2>
      {datasets.isPending && <p>Loading datasets…</p>}
      {datasets.isError && <p role="alert">{errorText(datasets.error)}</p>}
      {datasets.isSuccess && datasets.data.length === 0 && <p>No datasets yet. Upload a CSV when you start a run.</p>}
      {datasets.isSuccess && datasets.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Datasets</caption>
            <thead><tr><th>File</th><th>Rows</th><th>Columns</th><th>Uploaded</th></tr></thead>
            <tbody>
              {datasets.data.map((dataset) => (
                <tr key={dataset.id}>
                  <td>{dataset.filename}</td>
                  <td>{dataset.n_rows}</td>
                  <td>{dataset.n_cols}</td>
                  <td>{formatTime(dataset.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Contracts</h2>
      {contracts.isPending && <p>Loading contracts…</p>}
      {contracts.isError && <p role="alert">{errorText(contracts.error)}</p>}
      {contracts.isSuccess && contracts.data.length === 0 && <p>No saved contracts.</p>}
      {contracts.isSuccess && contracts.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Contracts</caption>
            <thead><tr><th>Version</th><th>Target</th><th>Saved</th><th>Contract</th></tr></thead>
            <tbody>
              {contracts.data.map((record) => {
                const target = getAt(record.contract, ['target', 'column']);
                return (
                  <tr key={record.id}>
                    <td>{record.version}</td>
                    <td>{typeof target === 'string' ? target : '—'}</td>
                    <td>{formatTime(record.created_at)}</td>
                    <td>
                      <details>
                        <summary>Show JSON</summary>
                        <pre><code>{JSON.stringify(record.contract, null, 2)}</code></pre>
                      </details>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <h2>Project memory</h2>
      {project.isPending && <p>Loading project memory…</p>}
      {project.isError && <p role="alert">{errorText(project.error)}</p>}
      {project.isSuccess && project.data.memory.length === 0 && (
        <p>Nothing remembered yet. Completed runs add what they learned here.</p>
      )}
      {project.isSuccess && project.data.memory.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Project memory</caption>
            <thead><tr><th>Fact</th><th>Value</th><th>From run</th><th>Recorded</th></tr></thead>
            <tbody>
              {project.data.memory.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.key}</td>
                  <td><code>{JSON.stringify(entry.value)}</code></td>
                  <td>{runLink(entry.source_run_id)}</td>
                  <td>{formatTime(entry.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
