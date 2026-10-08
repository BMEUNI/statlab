import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { controlRun, rewindRun } from '../api/client.ts';
import type { StreamStatus } from '../api/events.ts';
import type { ControlReply, RunSummary } from '../api/types.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText, formatTime } from '../ui/format.ts';
import { formatUsd, nodeLabel, runControls, statusTone, STATUS_LABELS, type RunView } from './model.ts';

type Props = {
  run: RunSummary;
  view: RunView | null;
  costUsd: number | null;
  streamStatus: StreamStatus;
  championLabel: string | null;
  navigate: (route: Route) => void;
};
type Action = { kind: 'control'; action: 'pause' | 'resume' | 'cancel' } | { kind: 'rewind'; target: string };

const STREAM_LABELS: Record<StreamStatus, string> = {
  connecting: 'Connecting',
  connected: 'Live',
  reconnecting: 'Reconnecting',
  closed: 'Closed',
  error: 'Stopped. Reload the page to reconnect.',
};

export default function RunHeader({ run, view, costUsd, streamStatus, navigate, championLabel }: Props) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState(view?.spine[0]?.name ?? '');
  const controls = runControls(run.status);
  const tone = statusTone(run.status);
  const mutation = useMutation<ControlReply | string, Error, Action>({
    mutationFn: (action) =>
      action.kind === 'control'
        ? controlRun(run.id, action.action)
        : rewindRun(run.id, action.target, crypto.randomUUID()),
    onSuccess: async (reply) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', run.id] }),
        queryClient.invalidateQueries({ queryKey: ['executions', run.id] }),
      ]);
      if (typeof reply === 'string') navigate({ name: 'run', runId: reply });
    },
  });
  const busy = mutation.isPending;
  const parent = run.forked_from_run_id;
  const sub = run.status === 'completed' && championLabel !== null
    ? `Champion: ${championLabel}`
    : run.current_node !== null ? `Now: ${nodeLabel(run.current_node)}` : '';

  return (
    <>
      <section className="run-band" aria-label="Run status">
        <div className="run-band-top">
          <div>
            <p className="run-band-kicker">{run.mode === 'guided' ? 'Guided run' : 'Autonomous run'}</p>
            <p className={`run-band-status tone-${tone}`}>{STATUS_LABELS[run.status]}</p>
            {sub !== '' && <p className="run-band-sub">{sub}</p>}
          </div>
          <div className="run-band-actions">
            {controls.pause && (
              <button type="button" disabled={busy} onClick={() => mutation.mutate({ kind: 'control', action: 'pause' })}>Pause</button>
            )}
            {controls.resume && (
              <button type="button" className="primary" disabled={busy} onClick={() => mutation.mutate({ kind: 'control', action: 'resume' })}>Resume</button>
            )}
            {controls.cancel && (
              <button
                type="button"
                className="danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm('Stop this run? It cannot be resumed.')) mutation.mutate({ kind: 'control', action: 'cancel' });
                }}
              >Stop the run</button>
            )}
          </div>
        </div>
        <dl className="run-stats">
          <div><dt>Agent cost</dt><dd>{costUsd === null ? '…' : formatUsd(costUsd)}</dd></div>
          {view?.budget && (
            <div>
              <dt>Fit time</dt>
              <dd>{`${(view.budget.usedMs / 1000).toFixed(1)} s of ${(view.budget.limitMs / 1000).toFixed(0)} s`}</dd>
            </div>
          )}
          <div><dt>Started</dt><dd>{formatTime(run.started_at)}</dd></div>
        </dl>
      </section>
      <details className="run-more">
        <summary>Run details and rewind</summary>
        <dl className="facts">
          <dt>Run ID</dt><dd><code>{run.id}</code></dd>
          <dt>Ended</dt><dd>{formatTime(run.ended_at)}</dd>
          <dt>Live updates</dt><dd>{STREAM_LABELS[streamStatus]}</dd>
          {parent !== null && (
            <>
              <dt>Rewound from</dt>
              <dd><a href={formatRoute({ name: 'run', runId: parent })}>run {parent}</a></dd>
            </>
          )}
        </dl>
        {controls.rewind && view !== null && (
          <>
            <div className="field">
              <label htmlFor="header-rewind-target">Start again from</label>
              <select id="header-rewind-target" value={target} onChange={(e) => setTarget(e.target.value)}>
                {view.spine.map((node) => <option key={node.name} value={node.name}>{nodeLabel(node.name)}</option>)}
              </select>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm('Start a new run from this step? This run will be replaced.')) {
                  mutation.mutate({ kind: 'rewind', target });
                }
              }}
            >Rewind</button>
          </>
        )}
      </details>
      {mutation.isSuccess && typeof mutation.data !== 'string' && <p className="ok">{mutation.data.text}</p>}
      {mutation.isError && <p role="alert">{errorText(mutation.error)}</p>}
    </>
  );
}
